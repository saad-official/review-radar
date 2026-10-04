# 0002 — Memory is tables: themes, proposals and human decisions, read through one tool

Date: 2026-10-04. Status: accepted. Code: [`db/store.py`](../../src/review_radar/db/store.py), [`db/postgres.py`](../../src/review_radar/db/postgres.py).

## Context

"The agent remembers what was already triaged" can be built many ways: a vector store of
past conversations, a summarised memory blob carried between runs, a framework's memory
module, or ordinary tables. The things worth remembering here are concrete: which themes
exist, which reviews belong to them, what was proposed, and what a human decided and why.

## Decision

Long-term memory is the relational data the product already needs:

- `themes` (with a normalised centroid embedding) and `theme_reviews`: new reviews are
  linked to an existing theme when cosine similarity to its centroid is ≥ 0.80, before any
  new theme is created. The centroid is updated as a running mean.
- `proposals` with `status`, `decision_reason`, `decided_by`: a rejection reason such as
  "declined: duplicate of #42" is stored on the row.
- `search_memory(query)` is the agent's only read path: Postgres full-text search (an OR
  query over the keywords) and pgvector cosine over theme centroids, fused with reciprocal
  rank fusion; proposals inherit their theme's vector rank so a rejected proposal surfaces
  for a similar new theme.
- **Constraints, not prompts**, make the important memory binding: partial unique indexes
  allow one reply proposal per review and one issue proposal per theme, ever. A tool call
  that would duplicate one is refused with the existing status and the human's reason.

## Consequences

- Memory is inspectable with SQL, editable by a human (resolve or ignore a theme), and
  covered by the same backups and migrations as everything else.
- No summarisation drift: nothing is paraphrased into memory by a model.
- Re-proposing a rejected theme is impossible even if the model ignores `search_memory`
  (tested: `test_rejected_theme_is_never_reproposed`).
- The memory is per app and only as good as the clustering threshold; a theme that drifts
  (centroid pulled by loosely related reviews) is the failure to watch. The thresholds are
  in `routing.toml` and swept in `docs/evals.md`.

## Alternatives

- **A separate vector store of agent transcripts.** Retrieves what the agent *said*, not
  what is true, and duplicates data Postgres already holds.
- **A memory summary passed between runs.** Cheap, but lossy and unauditable, and a
  prompt-injected review could poison it.
