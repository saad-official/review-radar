# API

Base URL: https://review-radar-api.vercel.app (local: http://localhost:7860). OpenAPI:
`/api/docs`, `/api/openapi.json`. All bodies are JSON unless noted.

**Auth.** Write routes need `Authorization: Bearer <OPERATOR_TOKEN>` (401 when wrong, 503
when the server has none configured). Read routes are public for apps with `public: true`
(the demo app) and need the operator token otherwise. CORS allows `Authorization`,
`Content-Type` and `Last-Event-ID` from `WEB_ORIGIN`.

**Errors.** Always `{"detail": {"code": "...", "message": "...", "retry_after"?: n}}`
(`Retry-After` header too). Codes used: `unauthorized`, `operator_auth_disabled`,
`app_not_found`, `app_exists`, `run_not_found`, `run_active`, `run_in_progress`,
`rate_limited`, `proposal_not_found`, `already_decided`, `proposal_rejected`,
`guardrail_failed`, `bad_edit`, `no_github_repo`, `no_github_token`,
`github_token_unreadable`, `encryption_unavailable`, `missing_column`, `missing_file`,
`empty_csv`, `cron_disabled`. FastAPI's own validation errors (422) keep FastAPI's shape.

## Apps

| Method and path | Auth | Notes |
|---|---|---|
| `GET /api/apps` | public | public apps; all apps with the operator token |
| `POST /api/apps` | operator | `{store: "ios"\|"android", store_id, country?="us", name?, github_repo?, policy?, public?, github_token?}` → 201 App. iOS names are looked up from iTunes when omitted |
| `GET /api/apps/{id}` | read | App |
| `PATCH /api/apps/{id}` | operator | any of `{name, github_repo, policy, public, github_token}`; `github_token: ""` removes it |
| `DELETE /api/apps/{id}` | operator | 204; cascades to everything |
| `POST /api/apps/{id}/import` | operator | CSV upload, see below → `{rows, imported, skipped, duplicates, errors: [{line, error}]}` |
| `GET /api/apps/{id}/events?limit=` | read | audit trail `[{id, actor, type, entity_type, entity_id, input, output, created_at}]` |

App:

```json
{
  "id": "uuid", "store": "ios", "store_id": "324684580", "name": "Spotify: Music and Podcasts",
  "country": "us", "github_repo": "saad-official/review-radar-demo-issues", "policy": "...",
  "public": true, "has_github_token": false, "created_at": "...",
  "counts": {"reviews": 200, "analysed": 30, "themes": 4, "proposals": {"proposed": 6, "approved": 1}},
  "new_reviews": 12,
  "last_run": {"id": "uuid", "status": "done", "trigger": "manual", "created_at": "...",
               "started_at": "...", "finished_at": "...", "summary": "...", "usd": 0.0123,
               "new_reviews": 12}
}
```

`new_reviews` is the number of reviews the latest run fetched that were new.

**CSV import.** `multipart/form-data` with the file in field `file` (or the CSV itself as
the body with `Content-Type: text/csv`). Header row required, names case-insensitive.
Documented columns: `store_review_id`, `rating`, `body`, `date` (required by the UI's
contract), optional `author`, `title`, `app_version`, `country` (ignored). The parser is
lenient: only a text column is strictly required; aliases accept the Play Console export
(`Review Text`, `Star Rating`, `App Version Name`, `Review Submit Date and Time`); a
missing id is derived from the row content. Invalid rows are reported and skipped;
re-importing is idempotent.

## Runs

| Method and path | Auth | Notes |
|---|---|---|
| `POST /api/apps/{id}/runs` | operator | body `{max_reviews?: 1-200}` → 202 `{id, status, process, process_url, events_url}`; 409 `run_active` if a run started < 30 min ago is unfinished; 429 when rate limited |
| `GET /api/apps/{id}/runs?limit=` | read | newest first, RunSummary[] (shape of `last_run` above) |
| `POST /api/runs/{id}/process` | operator (or QStash signature) | 200 `{id, status, resumable: false, message}` when finished; **202 `{resumable: true}`** when the time budget ran out (call again); 409 `run_in_progress` while another request holds the lease |
| `GET /api/runs/{id}` | read | Run |
| `GET /api/runs/{id}/steps?after=` | read | the trajectory, StepView[] |
| `GET /api/runs/{id}/events?after=&token=` | read | SSE, see below |

`process` is `"client"` on Vercel without QStash (the UI must POST `process_url`, repeating
while it answers 202), `"background"` locally, `"qstash"` when configured.

Run:

```json
{
  "id": "uuid", "app_id": "uuid", "status": "queued|fetching|extracting|embedding|clustering|proposing|done|failed",
  "trigger": "manual|cron|demo", "attempts": 1, "summary": "...", "error": null,
  "budget": {"max_usd": 0.1, "max_iterations": 24, "deadline_s": 200, "max_reviews": 50, "routing": {...}, "prompts": {...}},
  "usage": {"calls": 9, "failed_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0,
            "total_tokens": 0, "usd": 0.0, "max_usd": 0.1, "summary": "...", "prices_verified_on": "2026-09-05",
            "by_model": [{"model": "...", "provider": "...", "stage": "extract|theme|embed|memory|agent", "calls": 3,
                          "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "usd": 0.0}]},
  "stats": {"fetched": 50, "new_reviews": 12, "working_set": 30, "analysed": 30, "quarantined": 0,
            "categories": {"praise": 17, "billing": 6}, "themes_new": 3, "themes_linked": 1,
            "replies_proposed": 5, "issues_proposed": 1, "stop_reason": "final_answer|max_iterations|deadline|budget|nothing_to_propose",
            "iterations": 7, "budget_hit": false},
  "created_at": "...", "started_at": "...", "finished_at": "..."
}
```

StepView (one line of the flight recorder):

```json
{"seq": 12, "at": "...", "stage": "queued|fetch|extract|embed|cluster|propose|finish",
 "kind": "tool_call|tool_result|model|note", "name": "extract_signals",
 "args": {...}, "result": {...}, "usage": {"calls": 1, "model": "...", "prompt_tokens": 0,
 "completion_tokens": 0, "reasoning_tokens": 0, "usd": 0.0, "latency_s": 3.2, "errors": null} | null}
```

Names: notes `status` (result `{status}`), `quarantine`, `suspended`, `budget`,
`naming_failed`, `agent_loop` (args: budgets, prompt version), `agent_stopped` (result:
stop_reason, iterations, replies, issues, guardrail_checks, summary), `summary`; tool steps
`fetch_reviews`, `extract_signals`, `embed_reviews`, `cluster_reviews`, `search_memory`,
`draft_reply`, `propose_issue`, `summarise_run` (results of loop tools:
`{ok, content, detail}`; refusals have `ok: false` and the reason in `content`); `model`
steps are named after the model and carry `{content, tool_calls: [names]}`.

**SSE** (`text/event-stream`): unnamed events (`onmessage`), `id` = `seq` (reconnect with
`Last-Event-ID` or `?after=`), `data` = StepView plus `data` (= `result`; on stage changes
it carries `status`). The final status step arrives with `kind: "done"` or `kind: "failed"`
(and `data.status`); a run that ends without one gets a synthetic final event (`kind`
`done`, `failed` or `error`). Any of those ends the stream. Streams last at most 250 s; the
browser reconnects. Public apps need no token; otherwise pass `?token=`.

## Themes and reviews

`GET /api/apps/{id}/themes?status=open|resolved|ignored`, ranked by review count:

```json
{"id": "uuid", "title": "Too many ads between songs", "summary": "...", "kind": "bug|request|praise|billing|other",
 "status": "open", "review_count": 8, "quotes": [{"review_id": "uuid", "text": "verbatim excerpt"}],
 "sentiment": {"positive": 0, "neutral": 0, "negative": 7, "mixed": 1},
 "sentiment_by_day": [{"date": "2026-10-03", "positive": 0, "neutral": 0, "negative": 3, "mixed": 0}],
 "max_severity": 4, "avg_rating": 1.6,
 "issue_proposal": {"id": "uuid", "status": "proposed", "url": null} | null,
 "created_at": "...", "updated_at": "..."}
```

`GET /api/apps/{id}/reviews?theme=&limit=&offset=` newest first:

```json
{"id": "uuid", "store_review_id": "14622068328", "source": "appstore", "author": "...", "rating": 1,
 "title": "...", "body": "...", "app_version": "9.1.88", "date": "...",
 "signals": {"category": "bug", "sentiment": "negative", "severity": 4, "feature_area": "playback",
             "devices": [], "os_versions": [], "app_versions": [], "quotes": [], "flags": [],
             "model": "gemini-3.5-flash-lite", "prompt_version": "extract.v1@ab12cd34"} | null,
 "theme_ids": ["uuid"]}
```

`signals` are model-inferred (labelled by `model` and `prompt_version`); rating, version and
date are store facts. `flags` may contain `prompt_injection` (quarantined, `model: "rules"`)
or `extraction_missing`.

## Proposals (the approval queue)

| Method and path | Auth | Notes |
|---|---|---|
| `GET /api/apps/{id}/proposals?status=&kind=&run=` | read | newest first |
| `POST /api/proposals/{id}/approve` | operator | body `{draft?: {...}}` only when edited (`edits` is a synonym). Returns the updated Proposal; for an issue it creates it on GitHub and returns `result.url`/`result.number` (or `status: failed`, `result.error`). A repeat decision: 409 `already_decided` |
| `POST /api/proposals/{id}/reject` | operator | `{reason}` (1-500 chars) → updated Proposal; repeat: 409 |
| `GET /api/proposals/export.csv?app=` | read | approved replies as CSV (`proposal_id, store_review_id, review_date, rating, review_title, review_body, reply, approved_at, approved_by`) |

Approve edits: reply `{text}` or `{body}`; issue any of `{title, summary, body,
suspected_area, severity}` (an edited `body` is used as written, otherwise the body is
re-rendered). Edits go through the same guardrails (422 `guardrail_failed`).

Proposal:

```json
{
  "id": "uuid", "app_id": "uuid", "run_id": "uuid", "kind": "reply|issue",
  "status": "proposed|approved|rejected|executed|failed",
  "draft": {
    "text": "reply text (reply only)", "review_alias": "R3",
    "title": "...", "summary": "...", "suspected_area": "playback", "severity": 4,
    "evidence": [{"review_id": "uuid", "store_review_id": "...", "quote": "...", "date": "...", "rating": 1, "app_version": "9.1.88"}],
    "affected_versions": ["9.1.88"], "affected_devices": ["iPhone 15"], "devices": ["iPhone 15"],
    "theme_id": "uuid", "theme_title": "...", "review_count": 8, "labels": ["review-radar"],
    "body": "rendered GitHub markdown (issue only)", "edited": true
  },
  "reasoning": "why the agent proposed it",
  "guardrails": {"passed": true, "violations": [{"rule": "...", "detail": "..."}], "checks": ["length", "urls", "..."]},
  "decided_by": "operator", "decided_at": "...", "decision_reason": "duplicate of #42", "reason": "duplicate of #42",
  "result": {"url": "https://github.com/.../issues/7", "number": 7, "repo": "..."} | {"error": "github_forbidden", "message": "..."} | null,
  "review": {"id": "uuid", "store_review_id": "...", "rating": 1, "title": "...", "body": "...", "app_version": "...", "date": "..."} | null,
  "theme": {"id": "uuid", "title": "...", "kind": "bug", "review_count": 8} | null,
  "created_at": "...", "updated_at": "..."
}
```

## Cron and health

- `GET|POST /api/cron/daily` with `Authorization: Bearer $CRON_SECRET`: for each app,
  resume its unfinished run or start one (`trigger: cron`) and process within the request
  budget → `{apps: [{app_id, run_id, status, resumable}]}`.
- `GET /api/health` → `{ok, version, providers: {groq, gemini, voyage}, embeddings, db, store, dispatch, operator_auth, encryption}`.
