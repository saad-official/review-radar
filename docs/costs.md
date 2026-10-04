# Costs

Every measured number comes from llm-kit's `Ledger` (provider-reported tokens priced at
**paid** rates from `llm_kit.pricing`, verified 2026-09-05; `gemini-embedding-001` at USD
0.15 per 1M input tokens, registered in `embeddings.py` because llm-kit has no row for it,
token count estimated at 4 characters per token because the embeddings API reports none).
All calls ran on free tiers and cost $0.00; the numbers say what real traffic would cost.
Spec target: **under $1 per app per month** for a daily run over ~50 new reviews.

Prices (USD per 1M tokens, in / out): `openai/gpt-oss-20b` 0.075 / 0.30,
`openai/gpt-oss-120b` 0.15 / 0.60, `gemini-3.5-flash-lite` 0.30 / 2.50,
`gemini-embedding-001` 0.15 / 0.

## Measured (2026-10-04, two live attempts, Gemini only)

Groq's daily free quota was already spent, so both attempts ran with `--no-groq`: every
chat call went to the fallback, `gemini-3.5-flash-lite`, whose output tokens cost 8x
gpt-oss-20b's.

| Stage (30 reviews) | Calls | Tokens in / out | Cost | Latency | Source |
|---|---|---|---|---|---|
| extract (3 batches of 10) | 3 | 3,066 / 2,879 | $0.008117 | 3.1-5.5 s per call | attempt 2, `evals/fixtures/recorded_run.json` |
| extract (same 30, attempt 1) | 3 | 3,066 / 2,795 | $0.007907 | 3.0-5.4 s | attempt 1 console log |
| embed (30 reviews, one batch) | 1 | ~839 (estimated) / 0 | $0.000126 | 3.8 s | attempt 1 |
| theme naming (call + repair) | 2 | 2,450 / 730 | $0.002560 | | attempt 1 (failed validation, see below) |
| propose loop | 0 | | not measured | | both attempts stopped before it |

What happened:

1. **Attempt 1** extracted, embedded and clustered 30 reviews, then the theme-naming call
   came back without a `title` and the propose loop got a **400** from Gemini (*"schema at
   top-level requires unspecified property 'title'"*). Cause: llm-kit's schema normaliser
   strips every key named `title`, including a *property* called `title`, while `required`
   still lists it. Fixed in this project (theme field renamed to `name`; tool schemas built
   by `PortableTool` with a correct normaliser; regression tests in `tests/test_tools.py`).
   The llm-kit bug is an open item.
2. **Attempt 2** (after the fix, from a clean app) extracted the same 30 reviews and then
   got **429 quota exceeded** from the embeddings API. Per the run rules it stopped there
   and was not retried. The run is recorded as `failed` with `model_quota_exhausted`; the
   30 analysed reviews stay owed to the next run's embed stage.

So the measured cost of a full run is incomplete: extraction, embedding and naming are
measured, the propose loop is not.

## Projection for a daily run over 50 new reviews

Scaling the measured stages by 50/30 and estimating the loop (about 8 turns; context grows
from ~5k to ~9k input tokens per turn as tool results accumulate, ~300 output tokens per
turn: ~55k in / ~2.5k out per run):

| Stage | On Groq (primary routes) | On the Gemini fallback (measured route) |
|---|---|---|
| extract, 5 calls (5.1k in / 4.8k out + gpt-oss reasoning ~2k) | ~$0.0028 | $0.0135 (measured rate) |
| theme naming, 1 call | ~$0.0005 | ~$0.0013 |
| embeddings, 1 batch | $0.0002 | $0.0002 |
| propose loop (estimate) | ~$0.0098 on gpt-oss-120b | ~$0.0228 |
| **per run** | **~$0.013** | **~$0.038** |
| **per app per month (30 runs)** | **~$0.40** | **~$1.14** |

- On the primary routes the target holds with room to spare. On the Gemini fallback a
  month of daily runs would slightly exceed $1, because flash-lite's output tokens are
  8x the price; the per-run ledger ceiling ($0.10) bounds the worst case at $3 per app per
  month whatever happens.
- The propose loop is the largest line and the only estimate. It dominates because every
  turn re-sends the growing conversation (tool results included). The knobs, in order:
  fewer reply candidates in context (15 now), shorter tool results, fewer turns (the
  prompt asks for batched tool calls), or a smaller agent model.
- Cost per review flagged as quarantined is $0: those never reach a model.

## How to complete these numbers

`uv run python scripts/smoke_live.py` (one run, ~15-20 calls) when quota allows; it
records `evals/fixtures/recorded_run.json`, and `uv run evals` regenerates the cost section
of `docs/evals.md` from it. Then replace the estimate rows above with the measured ones.
