"""PostgresStore against a real Postgres with pgvector (CI service container, or any
disposable database): `TEST_DATABASE_URL=... uv run pytest -q -m postgres`.

The same end-to-end run as the MemoryStore tests, plus what only Postgres can show: the
migration applies, pgvector nearest-neighbour works, the one-proposal-per-theme index holds,
and run_steps / agent_events refuse UPDATE and DELETE (but still cascade with their app).
"""

import os

import pytest

from review_radar.service import ProposalService, RunService, create_app

from .conftest import make_engine

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set"),
    # The fixture drops the schema: never let it near the real (Neon) database.
    pytest.mark.skipif("neon.tech" in (DSN or ""), reason="refusing to drop a Neon schema"),
]


@pytest.fixture
def pg_store():
    import psycopg

    from review_radar.db.migrate import apply_migrations
    from review_radar.db.postgres import PostgresStore

    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS review_radar CASCADE")
    apply_migrations(DSN)
    store = PostgresStore(DSN)
    yield store
    store.close()


def test_end_to_end_on_postgres(settings, pg_store):
    import psycopg

    engine = make_engine(settings, store=pg_store)
    app = create_app(
        engine,
        store_kind="ios",
        store_id="1",
        name="pg",
        github_repo="a/b",
        github_token="t-123456789",
    )
    service = RunService(engine)
    run = service.create_run(app.id)
    assert service.process(run.id).status == "done"
    finished = pg_store.get_run(run.id)
    assert finished.stats["issues_proposed"] == 1 and finished.usage["calls"] > 0
    issue = pg_store.list_proposals(app.id, kind="issue")[0]
    theme = pg_store.get_theme(issue.theme_id)
    nearest = pg_store.nearest_themes(app.id, theme.embedding, limit=1)
    assert nearest[0][0].id == theme.id and nearest[0][1] > 0.99
    assert ProposalService(engine).approve(issue.id).status == "executed"
    duplicate = issue.model_copy(update={"id": "00000000-0000-0000-0000-000000000001"})
    assert pg_store.create_proposal(duplicate) is None  # one issue proposal per theme
    reply = pg_store.list_proposals(app.id, kind="reply")[0]
    ProposalService(engine).reject(reply.id, reason="duplicate of #42")
    hits = pg_store.search_memory(app.id, "duplicate", None)
    assert any(h.decision_reason == "duplicate of #42" for h in hits)

    with psycopg.connect(DSN) as conn:
        for sql in (
            "UPDATE review_radar.run_steps SET name = 'x' WHERE run_id = %s",
            "DELETE FROM review_radar.run_steps WHERE run_id = %s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute(sql, (run.id,))
    assert pg_store.delete_app(app.id)  # cascades through the append-only tables
    assert pg_store.get_run(run.id) is None
