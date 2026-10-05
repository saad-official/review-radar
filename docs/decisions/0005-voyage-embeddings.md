# 0005 — Voyage `voyage-4-lite` embeddings, dimension as configuration

Date: 2026-10-05. Status: accepted. Code: [`embeddings.py`](../../src/review_radar/embeddings.py), [`config.py`](../../src/review_radar/config.py), [`db/migrate.py`](../../src/review_radar/db/migrate.py), [`0002_embedding_dimensions.sql`](../../src/review_radar/db/migrations/0002_embedding_dimensions.sql).

## Context

Embeddings were Gemini `gemini-embedding-001` only, with 768 dimensions hard-coded in the
embedder and in `vector(768)` columns. Gemini's free tier counts **each embedded text as one
request: 1,000 per day per project**, and that project's quota is shared with DocPilot.
DocPilot's ingestion alone can spend the day's 1,000, after which every Review Radar run
fails at the embed stage (the live smoke did: `429 ... RESOURCE_EXHAUSTED`).

## Decision

- Add `VoyageEmbedder` (`voyage-4-lite`, `POST https://api.voyageai.com/v1/embeddings`),
  selected with `EMBEDDING_PROVIDER=voyage` + `VOYAGE_API_KEY` (the same key as DocPilot).
  `input_type` is `document` for reviews and theme centroids and `query` for the agent's
  memory search. Voyage reports `usage.total_tokens`, so the ledger records real tokens
  (Gemini's are estimated), priced at USD 0.02 per 1M.
- Make the dimension configuration: `EMBEDDING_DIMENSIONS` (default 768) is passed to the
  embedder *and* rendered into the migrations by `uv run migrate` (the `{{EMBEDDING_DIMENSIONS}}`
  placeholder, as in DocPilot), so the vectors and the columns cannot disagree.
  `voyage-4-lite` only offers 256/512/1024/2048, so the Voyage deployment uses **1024**.
- Migration `0002_embedding_dimensions.sql` retypes `reviews.embedding` and
  `themes.embedding` to `vector(EMBEDDING_DIMENSIONS)` with `USING NULL`, only when the
  current type differs (re-running it at the same dimension keeps the vectors).
- Same failure contract as Gemini: 429/5xx/transport errors are `LLMTransientError`, the
  run fails as `model_quota_exhausted` / `model_unavailable` with its signals kept. Voyage
  additionally retries a 429/5xx up to 3 attempts within 30 s (Retry-After honoured),
  because without a payment method its limit is per *minute* (3 RPM / 10K TPM), not per day.

## Quota arithmetic

| | Gemini free tier | Voyage free allowance |
|---|---|---|
| Unit | 1,000 embedded texts / day / project | 200M tokens / account, once |
| Shared with | DocPilot (same Google project) | DocPilot (same key) |
| One daily run, 50 reviews (~28 tokens each, estimated in the live smoke) + ~3 memory queries | 53 of 1,000 texts/day, *if DocPilot left any* | ~1,500 tokens/day |
| How long the allowance lasts for Review Radar | resets daily, but contended | 200M / 1,500 ≈ 133,000 days |
| Paid rate after that | $0.15 / 1M tokens | $0.02 / 1M tokens (~$0.00003/day) |

The binding Voyage limit is the rate (3 requests/minute) until a payment method is added;
a run makes one embed call per 100 reviews plus one per memory search, which fits.

## Consequences

- Switching model or dimension re-embeds: 0002 nulls every stored vector (the 30 Gemini
  vectors are meaningless next to Voyage's). Recovery is automatic: an analysed review with
  `embedding IS NULL` is in the next run's working set (`unprocessed_review_ids`), and the
  embed stage rebuilds the centroid of any theme that lost it from its re-embedded members,
  so existing themes stay linkable instead of being duplicated.
- The migration runs once per database (the runner records it by name). Changing the
  dimension again later needs a new migration file (copy 0002 to 000N); the
  "only when different" guard makes such a copy safe to re-render.
- Clustering thresholds in `routing.toml` were tuned on Gemini/hash vectors; re-record the
  sweep with `uv run evals --record-embeddings` (it now uses the configured provider).
- Rejected: keeping Gemini on a second Google project (still 1,000/day, and a per-project
  quota workaround rather than a fix); local `nomic-embed-text` via Ollama (no Ollama on the
  serverless deploy).
