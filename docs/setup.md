# Setup

## Requirements

- Python 3.12 via `uv` (the project pins `.python-version`).
- Optional: a Postgres with pgvector (Neon Free works), a Gemini key (extraction fallback
  and embeddings), a Groq key (primary chat models), a GitHub fine-grained PAT.

## Local, no keys (in-memory store, offline embedder)

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
uv run evals --no-write                      # recorded evals, no network
EMBEDDING_PROVIDER=hash OPERATOR_TOKEN=dev uv run uvicorn review_radar.api.main:app --port 7860
```

OpenAPI: http://localhost:7860/api/docs. Without `DATABASE_URL` the store is in memory
(fine for trying the API; nothing survives a restart).

## With the real services

```bash
bash scripts/setup-env.sh          # from Git Bash: writes .env and web/.env.local, pushes Vercel env, migrates
# or by hand: copy .env.example to .env and fill DATABASE_URL, DATABASE_DIRECT_URL,
# GEMINI_API_KEY, GROQ_API_KEY, OPERATOR_TOKEN, APP_ENCRYPTION_KEY, CRON_SECRET
uv run migrate                     # applies db/migrations/*.sql once each (uses DATABASE_DIRECT_URL)
uv run demo                        # registers the demo app (demo.toml), seeds up to 200 reviews, no model calls
uv run demo --from-fixture         # seeds the 200 labelled eval reviews instead
uv run demo --live --max-reviews 30   # one agent run with real models (uses quota)
uv run uvicorn review_radar.api.main:app --port 7860
```

`scripts/setup-env.sh` generates `review-radar-app-encryption-key.txt`,
`review-radar-operator-token.txt` and `review-radar-ip-hash-salt.txt` in
`G:\Vibe Engineering Apps\.secrets` when missing, and reads the optional
`review-radar-github-token.txt`.

## Live smoke and evals

```bash
uv run python scripts/smoke_live.py --no-groq   # one real run over 30 labelled reviews; writes evals/fixtures/recorded_run.json
uv run evals --record-embeddings                # 1 embedding call: vectors for the clustering sweep
uv run evals --live                             # 20 extraction calls over all 200 labelled reviews
uv run evals                                    # recorded mode: rewrites docs/evals.md
```

`--no-groq` skips Groq when its daily free quota is spent (otherwise every call first
burns a 429). The smoke script stops on a quota error and never retries.

## Deploy (Vercel)

Two projects, as for Changelog Forge:

- `review-radar-api`: repository root, framework `fastapi` (root `main.py` re-exports the
  app), cron `GET /api/cron/daily` at 05:00 UTC (`vercel.json`). URL
  https://review-radar-api.vercel.app.
- `review-radar`: root directory `web/`. URL https://getreviewradar.vercel.app.

```bash
vercel deploy --prod --yes                 # API, from the repo root
(cd web && vercel deploy --prod --yes)     # web
```

Runs are processed inside `POST /api/runs/{id}/process` requests (≤ 240 s each; the UI
repeats the call while it answers 202). Set `QSTASH_TOKEN` + `PUBLIC_API_URL` to have
Upstash QStash call `/process` instead.

## CI

`.github/workflows/ci.yml`: lint, format check, tests, recorded evals; a second job runs
the Postgres tests against a `pgvector/pgvector:pg16` service (`-m postgres`); a third
builds the web app when `web/package.json` exists.
