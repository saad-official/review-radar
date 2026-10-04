import json

import httpx
import pytest

from review_radar.api.main import create_app
from review_radar.db import MemoryStore

from .conftest import FakeGitHub, make_engine

pytestmark = pytest.mark.anyio
OP = {"Authorization": "Bearer op-test"}


def client_for(engine) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(engine)), base_url="http://t"
    )


def sse(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        data = [line[6:] for line in block.splitlines() if line.startswith("data: ")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


async def make_app(client, **extra):
    body = {
        "store": "ios",
        "store_id": "123",
        "name": "Demo",
        "github_repo": "acme/issues",
        **extra,
    }
    response = await client.post("/api/apps", json=body, headers=OP)
    assert response.status_code == 201, response.text
    return response.json()


async def test_health(settings):
    async with client_for(make_engine(settings)) as client:
        body = (await client.get("/api/health")).json()
    assert body["ok"] and body["store"] == "memory" and body["dispatch"] == "background"
    assert body["operator_auth"] and body["encryption"] and body["embeddings"] == "hash"


async def test_write_routes_need_the_operator_token(settings):
    async with client_for(make_engine(settings)) as client:
        body = {"store": "ios", "store_id": "1", "name": "x"}
        missing = await client.post("/api/apps", json=body)
        wrong = await client.post("/api/apps", json=body, headers={"Authorization": "Bearer no"})
        assert missing.status_code == wrong.status_code == 401
        assert missing.json()["detail"]["code"] == "unauthorized"
        created = await make_app(client, public=False)
        assert (await client.get(f"/api/apps/{created['id']}")).status_code == 401
        assert (await client.get("/api/apps")).json() == []
        assert len((await client.get("/api/apps", headers=OP)).json()) == 1


async def test_operator_auth_disabled(settings):
    engine = make_engine(settings.model_copy(update={"operator_token": None}))
    async with client_for(engine) as client:
        response = await client.post(
            "/api/apps", json={"store": "ios", "store_id": "1"}, headers=OP
        )
    assert response.status_code == 503


async def test_app_crud_and_token_never_returned(settings):
    async with client_for(make_engine(settings)) as client:
        created = await make_app(client, public=True, github_token="github_pat_abcdefgh12345678")
        assert created["has_github_token"] and "github_token" not in json.dumps(created).replace(
            "has_github_token", ""
        )
        duplicate = await client.post(
            "/api/apps", json={"store": "ios", "store_id": "123", "name": "x"}, headers=OP
        )
        assert duplicate.status_code == 409
        bad = await client.post(
            "/api/apps", json={"store": "ios", "store_id": "9", "github_repo": "nope"}, headers=OP
        )
        assert bad.status_code == 422
        patched = await client.patch(
            f"/api/apps/{created['id']}",
            json={"policy": "allow_urls: true", "github_token": ""},
            headers=OP,
        )
        assert patched.json()["policy"] == "allow_urls: true"
        assert patched.json()["has_github_token"] is False
        public = await client.get(f"/api/apps/{created['id']}")
        assert public.status_code == 200 and public.json()["counts"]["reviews"] == 0
        deleted = await client.delete(f"/api/apps/{created['id']}", headers=OP)
        assert deleted.status_code == 204
        assert (await client.get(f"/api/apps/{created['id']}")).status_code == 404


async def test_run_lifecycle_and_read_routes(settings):
    engine = make_engine(settings)
    async with client_for(engine) as client:
        app = await make_app(client, public=True)
        created = await client.post(f"/api/apps/{app['id']}/runs", json={}, headers=OP)
        assert created.status_code == 202
        run = created.json()
        assert run["process"] == "background" and run["events_url"].endswith("/events")

        # The background task ran after the 202 (ASGITransport waits for it).
        view = (await client.get(f"/api/runs/{run['id']}")).json()
        assert view["status"] == "done" and view["stats"]["issues_proposed"] == 1
        assert view["usage"]["calls"] > 0 and view["budget"]["max_usd"] == 0.1
        assert "client_key" not in view

        steps = (await client.get(f"/api/runs/{run['id']}/steps")).json()
        assert steps[0]["seq"] == 1 and {s["kind"] for s in steps} >= {"tool_call", "model", "note"}
        tail = (await client.get(f"/api/runs/{run['id']}/steps?after=5")).json()
        assert tail[0]["seq"] == 6

        stream = await client.get(f"/api/runs/{run['id']}/events")
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "event:" not in stream.text
        events = sse(stream.text)
        assert [e["seq"] for e in events] == [s["seq"] for s in steps]
        assert events[-1]["kind"] == "done" and events[-1]["data"]["status"] == "done"
        stage_changes = [e["data"]["status"] for e in events if e["name"] == "status"]
        assert stage_changes[:3] == ["queued", "fetching", "extracting"]
        resumed = await client.get(
            f"/api/runs/{run['id']}/events", headers={"Last-Event-ID": str(len(steps) - 1)}
        )
        assert [e["seq"] for e in sse(resumed.text)] == [len(steps)]

        themes = (await client.get(f"/api/apps/{app['id']}/themes")).json()
        assert themes and themes[0]["review_count"] >= 2 and "embedding" not in themes[0]
        bug = next(t for t in themes if t["kind"] == "bug")
        assert bug["issue_proposal"]["status"] == "proposed" and bug["max_severity"] == 5
        assert sum(bug["sentiment"].values()) == bug["review_count"]
        assert bug["sentiment_by_day"][0]["date"] == "2026-10-03"

        reviews = (await client.get(f"/api/apps/{app['id']}/reviews?theme={bug['id']}")).json()
        assert len(reviews) == bug["review_count"]
        assert reviews[0]["signals"]["category"] == "bug" and bug["id"] in reviews[0]["theme_ids"]
        assert reviews[0]["signals"]["model"] == "openai/gpt-oss-20b"

        proposals = (await client.get(f"/api/apps/{app['id']}/proposals?status=proposed")).json()
        kinds = sorted(p["kind"] for p in proposals)
        assert kinds == ["issue", "reply", "reply", "reply"]
        reply = next(p for p in proposals if p["kind"] == "reply")
        assert reply["review"]["body"] and reply["guardrails"]["passed"]

        runs = (await client.get(f"/api/apps/{app['id']}/runs")).json()
        assert runs[0]["id"] == run["id"] and runs[0]["usd"] > 0
        detail = (await client.get(f"/api/apps/{app['id']}")).json()
        assert detail["last_run"]["status"] == "done" and detail["counts"]["analysed"] == 12
        assert detail["last_run"]["started_at"] and detail["new_reviews"] == 12
        events = (await client.get(f"/api/apps/{app['id']}/events")).json()
        assert {"run.created", "run.completed", "proposal.created"} <= {e["type"] for e in events}


async def test_approve_reject_and_export(settings):
    engine = make_engine(settings.model_copy(update={}))
    async with client_for(engine) as client:
        app = await make_app(client, public=True, github_token="github_pat_abcdefgh12345678")
        await client.post(f"/api/apps/{app['id']}/runs", json={}, headers=OP)
        proposals = (await client.get(f"/api/apps/{app['id']}/proposals")).json()
        issue = next(p for p in proposals if p["kind"] == "issue")
        replies = [p for p in proposals if p["kind"] == "reply"]

        assert (await client.post(f"/api/proposals/{issue['id']}/approve")).status_code == 401
        approved = await client.post(f"/api/proposals/{issue['id']}/approve", headers=OP)
        assert approved.status_code == 200
        assert approved.json()["status"] == "executed"
        assert approved.json()["result"]["url"].startswith("https://github.com/acme/issues/")
        again = await client.post(f"/api/proposals/{issue['id']}/approve", headers=OP)
        assert again.status_code == 409 and again.json()["detail"]["code"] == "already_decided"
        assert len(FakeGitHub.created) == 1
        executed = approved.json()
        assert executed["result"]["number"] == 1 and executed["run_id"]
        assert executed["draft"]["devices"] == executed["draft"]["affected_devices"]
        assert executed["draft"]["suspected_area"] == "app startup"

        edited = await client.post(
            f"/api/proposals/{replies[0]['id']}/approve",
            json={"draft": {"body": "Thanks, we hear you. Please reach support in the app."}},
            headers=OP,
        )
        assert edited.json()["draft"]["text"].startswith("Thanks, we hear you")
        bad = await client.post(
            f"/api/proposals/{replies[1]['id']}/approve",
            json={"edits": {"text": "Refund coming by Monday"}},
            headers=OP,
        )
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "guardrail_failed"
        rejected = await client.post(
            f"/api/proposals/{replies[2]['id']}/reject", json={"reason": "spam"}, headers=OP
        )
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["decision_reason"] == rejected.json()["reason"] == "spam"
        rejected_again = await client.post(
            f"/api/proposals/{replies[2]['id']}/reject", json={"reason": "x"}, headers=OP
        )
        assert rejected_again.status_code == 409
        conflict = await client.post(f"/api/proposals/{replies[2]['id']}/approve", headers=OP)
        assert conflict.status_code == 409

        export = await client.get(f"/api/proposals/export.csv?app={app['id']}")
        assert export.headers["content-type"].startswith("text/csv")
        lines = export.text.strip().splitlines()
        assert lines[0].startswith("proposal_id,store_review_id") and len(lines) == 2
        assert "Thanks, we hear you" in lines[1]


async def test_import_csv(settings):
    async with client_for(make_engine(settings)) as client:
        app = await make_app(client, public=True)
        csv_text = "review_id,rating,body\nx1,1,Crashes\nx2,9,Bad\nx1,1,Crashes\n"
        url = f"/api/apps/{app['id']}/import"
        response = await client.post(
            url, content=csv_text, headers={**OP, "Content-Type": "text/csv"}
        )
        body = response.json()
        assert body["rows"] == 3 and body["imported"] == 1 and body["skipped"] == 2
        assert body["duplicates"] == 0
        assert body["errors"][0]["line"] == 3
        again = (
            await client.post(url, content=csv_text, headers={**OP, "Content-Type": "text/csv"})
        ).json()
        assert again["imported"] == 0 and again["duplicates"] == 1
        multipart = await client.post(
            url,
            files={"file": ("r.csv", "store_review_id,rating,body,date\nm1,2,Slow,2026-10-01\n")},
            headers=OP,
        )
        assert multipart.json()["imported"] == 1
        bad = await client.post(url, content="a,b\n1,2\n", headers=OP)
        assert bad.status_code == 400 and bad.json()["detail"]["code"] == "missing_column"


async def test_process_endpoint_and_active_run_conflict(settings):
    engine = make_engine(settings.model_copy(update={"vercel": True}))
    async with client_for(engine) as client:
        app = await make_app(client)
        run = (await client.post(f"/api/apps/{app['id']}/runs", json={}, headers=OP)).json()
        assert run["process"] == "client"
        conflict = await client.post(f"/api/apps/{app['id']}/runs", json={}, headers=OP)
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "run_active"
        assert (await client.post(run["process_url"])).status_code == 401
        done = await client.post(run["process_url"], headers=OP)
        assert done.status_code == 200 and done.json()["status"] == "done"
        repeat = await client.post(run["process_url"], headers=OP)
        assert repeat.json()["message"] == "already finished"
        missing = await client.post("/api/runs/nope/process", headers=OP)
        assert missing.status_code == 404
        private = await client.get(f"/api/runs/{run['id']}/events")
        assert private.status_code == 401
        with_token = await client.get(f"/api/runs/{run['id']}/events?token=op-test")
        assert with_token.status_code == 200


async def test_cron_daily(settings):
    store = MemoryStore()
    engine = make_engine(settings, store)
    async with client_for(engine) as client:
        await make_app(client)
        assert (await client.get("/api/cron/daily")).status_code == 401
        response = await client.get(
            "/api/cron/daily", headers={"Authorization": "Bearer cron-test"}
        )
        assert response.status_code == 200
        [result] = response.json()["apps"]
        assert result["status"] == "done" and not result["resumable"]
        run = store.get_run(result["run_id"])
        assert run.trigger == "cron"


async def test_cors_preflight_allows_the_operator_bearer(settings):
    async with client_for(make_engine(settings)) as client:
        response = await client.options(
            "/api/runs/x/process",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    assert response.status_code == 200
    assert "authorization" in response.headers["access-control-allow-headers"].lower()
