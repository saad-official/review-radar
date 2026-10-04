# 0003 — The approval gate: every write is a proposal; executors run only on approval

Date: 2026-10-04. Status: accepted. Code: [`service.py`](../../src/review_radar/service.py) (`ProposalService`), [`executors/`](../../src/review_radar/executors/).

## Context

The agent reads text written by anonymous strangers and produces artefacts that speak for
the team (replies) or land in its tracker (issues). A wrong reply promises a refund; a
wrong issue wastes a sprint; a manipulated one is a security incident. The question is not
how good the model is, but what happens when it is wrong.

## Decision

1. **The agent cannot write outside its own run.** Its two write tools insert proposals
   with status `proposed`. Executors live in `executors/`, which nothing under `agent/`
   imports (a test parses the imports to enforce it).
2. **A human approves with the operator token.** `POST /api/proposals/{id}/approve`
   (optionally with edits) and `POST /api/proposals/{id}/reject` (reason required).
3. **Edits are re-checked.** An edited reply or issue passes through the same guardrails
   as the agent's draft; a human cannot accidentally approve a refund promise either.
4. **Executors act on the stored proposal**, never on model output produced at approval
   time.
5. **State machine** (one conditional `UPDATE` per transition):

   ```
   proposed --approve--> approved --issue executor--> executed  (result: url, number)
       |                     \----------------------> failed    (result: error) --approve--> ...
       +--reject(reason)--> rejected
   ```

   The `proposed|failed -> approved` transition is atomic, so concurrent approvals create
   at most one issue. The service is idempotent (approving an executed proposal returns it);
   the HTTP API answers a repeat decision with **409 `already_decided`** so the UI knows.
6. **Preconditions fail before state changes**: no repository or no token is a 422 and the
   proposal stays `proposed`.

## Consequences

- The worst case of a fooled agent is a bad proposal in a queue, next to its evidence and
  reasoning, not an action in the world.
- Replies are exported (CSV) rather than posted: store APIs need developer credentials the
  MVP does not hold. Approval is the record of consent; export is a read.
- A GitHub failure after the issue was actually created (a timeout on the response) could
  produce a duplicate on retry. The body carries `<!-- review-radar:proposal:{id} -->` so a
  future executor can search for it first; not done in the MVP.

## Alternatives

- **Auto-execute above a confidence threshold.** Model confidence is not calibrated
  against manipulation; rejected for the MVP.
- **Approval inside the agent loop** (a "request approval" tool that blocks). Holds a
  serverless request open for a human; the queue is the asynchronous version of the same idea.
