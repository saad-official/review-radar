# Architecture

Review Radar turns app-store reviews into triaged themes, reply drafts and GitHub issue
proposals. It is an agent in the narrow, useful sense: a **deterministic workflow** with
**one bounded judgement loop**, whose every write is a **proposal** a human approves.

```
scheduler: Vercel Cron (GET /api/cron/daily) · "Run now" in the UI · QStash (optional)
   │
   ├─ POST /api/apps/{id}/runs ──► runs row (queued) + budget snapshot
   └─ POST /api/runs/{id}/process  (≤ 240 s per request, lease-guarded, resumable)
        │
        │  RunWorkflow (agent/workflow.py), one checkpoint per stage
        │   1 fetch     App Store RSS (pages 1-10, stop at 400 / empty / known page)
        │               -> reviews (unique per app); working set = 50 newest unprocessed
        │   2 extract   injection detector quarantines obvious attacks (never sent to a model)
        │               complete_structured(ReviewSignalsBatch), 10 reviews per call,
        │               cheap tier (gpt-oss-20b -> gemini-3.5-flash-lite) -> signals
        │   3 embed     voyage-4-lite 1024-d (or gemini-embedding-001 768-d), batches of 100
        │               -> reviews.embedding; themes that lost a centroid get it rebuilt
        │   4 cluster   memory first: pgvector nearest theme, cosine >= 0.80 joins it;
        │               rest: average-linkage agglomerative (>= 0.78) within theme kind;
        │               one call names new clusters (quotes verified verbatim) -> themes
        │   5 propose   llm_kit.call_tools: search_memory · draft_reply · propose_issue ·
        │               summarise_run; 24 iterations, 200 s, Ledger $0.10 for the whole run;
        │               guardrails in code; tools only INSERT status='proposed'
        │   6 finish    stats, summary, usage (ledger at paid rates)
        │
        │  every stage, model turn, tool call and tool result -> run_steps (append-only)
        ▼
   approval queue (web UI) ──► POST /api/proposals/{id}/approve (operator token)
                               ├─ reply: status approved -> GET /api/proposals/export.csv
                               └─ issue: executors/github.py creates the issue (per-app
                                  token, AES-256-GCM at rest, or GITHUB_TOKEN) -> executed
```

## Modules

| Path | Responsibility |
|---|---|
| `ingest/` | `ReviewSource` protocol; `appstore.py` (RSS JSON and XML), `csv.py` (import schema), `googleplay.py` (`not_supported` stub pointing at CSV import) |
| `agent/workflow.py` | The run: stages, checkpoints, suspension, trajectory recording |
| `agent/extract.py`, `agent/cluster.py` | Extraction schema and post-checks; clustering math and theme naming |
| `agent/tools.py` | The four tools, alias ids, idempotency, budgets per run |
| `agent/guardrails.py`, `agent/injection.py`, `agent/templates.py` | Deterministic checks, the injection detector, the issue template |
| `llm.py`, `routing.toml` | Provider layer over llm-kit: fallback routes, pacing, quota cooldown, routed `call_tools`, Gemini thought-signature shim |
| `embeddings.py` | `EmbeddingProvider` protocol, Voyage, Gemini and offline hash implementations, `make_embedder` (EMBEDDING_PROVIDER, EMBEDDING_DIMENSIONS), ledger accounting |
| `service.py` | Use cases: apps, runs (lease, resume), the approval state machine, audit events |
| `executors/` | The only code that writes outside the database (GitHub issues, CSV export). Never imported by `agent/` (a test enforces it) |
| `db/` | `Store` protocol, `MemoryStore`, `PostgresStore` (psycopg 3 + pgvector), SQL migrations |
| `api/` | FastAPI routes, schemas, rate limiting, SSE |
| `evals/` | Scorers, keyword baseline, recorded-run export, `uv run evals` |

## Data model (schema `review_radar`)

Spec section 5, plus additions marked `ADDED` in `db/migrations/0001_init.sql`:
`apps.public` and `apps.github_token_ciphertext`; `reviews.source` and uniqueness per app;
`signals.feature_area`, `app_versions`, `flags`; `themes.quotes`, provenance and a generated
`tsvector`; `theme_reviews.similarity` and `run_id`; `runs.stats`, `trigger`, lease columns;
`run_checkpoints`; `proposals.decision_reason`; `run_steps.stage`; partial unique indexes
(one reply proposal per review, one issue proposal per theme, ever); triggers that make
`run_steps` and `agent_events` append-only (cascading deletes from a parent still work).

## Run lifecycle and durability

`POST /process` claims a lease (`attempts < 4`, lease expired or empty) with one
conditional `UPDATE`, so a QStash delivery, the UI and the cron can race safely. Each stage
checks the clock; with less than 20 s left the run is *suspended*: the lease is released,
the attempt is given back, the ledger is checkpointed, and the call answers **202
`{resumable: true}`**. The propose loop only starts with at least 80 s left and its own
deadline is `min(200 s, remaining - 20 s)`. Provider failures end the run as `failed` with
`model_quota_exhausted` or `model_unavailable`; work already stored (signals, embeddings,
themes) is kept, and the next run picks up reviews that still owe a stage.

## Memory

Themes and proposals are the long-term memory (decision 0002). Three mechanisms:
linking new reviews to existing theme centroids before clustering; `search_memory`
(Postgres full text OR-query + pgvector cosine, fused with reciprocal rank fusion) that
surfaces past themes and human decisions; and constraints (unique indexes) that make
"never re-propose what a human rejected" a database fact rather than a prompt request.

## Why the numbers

- **10 reviews per extraction call**: the system prompt (~600 tokens) is amortised to
  ~60 tokens per review, and a failed batch is cheap to retry on the fallback route.
- **24 iterations, 200 s, $0.10**: three different failure modes (a looping model, a
  hanging provider, an expensive model) need three different limits.
- **≤ 240 s per `/process`**: Vercel's Python functions stop at 300 s; the margin covers
  the response and a slow database write.

## Local latency note

From a laptop far from the Neon region every query is a ~1 s round trip, so a 30-review
run took ~6 minutes locally, almost all of it database latency (the model calls were 3-6 s
each). Inserts are pipelined (`executemany(returning=True)`); on Vercel, next to Neon,
round trips are milliseconds.
