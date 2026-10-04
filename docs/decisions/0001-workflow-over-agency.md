# 0001 — Workflow over agency: the model decides what to propose, never whether to write

Date: 2026-10-04. Status: accepted. Code: [`agent/workflow.py`](../../src/review_radar/agent/workflow.py), [`agent/tools.py`](../../src/review_radar/agent/tools.py).

## Context

The spec lists seven tools (`fetch_reviews`, `extract_signals`, `search_memory`,
`cluster_reviews`, `draft_reply`, `propose_issue`, `summarise_run`). The fully agentic
version hands all seven to one tool loop and lets the model plan the run. That design is
easy to demo and hard to operate: the order of stages varies run to run, a model that
forgets to extract or cluster silently produces a worse run, each stage costs a full
round trip of context, and a stuck loop can spend the budget fetching the same page.

## Decision

The run is a fixed workflow: fetch → extract → embed → cluster → propose → finish, each
stage checkpointed. Only **propose** is a tool loop (`llm_kit.call_tools`), with four
tools: `search_memory`, `draft_reply`, `propose_issue`, `summarise_run`. The deterministic
stages are still recorded as `tool_call` / `tool_result` steps under the spec's tool names,
so the trajectory reads the same, but code decides that they run.

In the loop the model decides **which** themes deserve an issue, **which** reviews deserve
a reply, **when** to consult memory, and **how** to phrase things. It does not decide
whether a write happens: both write tools insert a row with status `proposed`, and only
the approve endpoint can act on one (decision 0003).

Where the spec said `draft_reply(review_id)` and `propose_issue(theme_id)`, the tools take
the text as validated arguments (`draft_reply(review_id, text, reasoning)`,
`propose_issue(theme_id, title, summary, suspected_area, severity, evidence[], reasoning)`)
instead of each tool calling a model of its own. One loop writes everything: no nested
model call per proposal (quota and latency), and a guardrail failure returns to the same
model as a tool error it can fix once.

Bounds: 24 iterations, a 200 s deadline (less if the request has less time left), and a
$0.10 ledger ceiling that covers the whole run including extraction and embeddings.

## Consequences

- Runs are comparable: the same stages, in the same order, with per-stage costs.
- Resumability is simple: checkpoints are per stage, and only one stage is open-ended.
- The agent cannot skip or reorder the workflow, which also means it cannot decide to
  fetch more reviews when it wants context. That trade is deliberate for a triage tool.
- Ids in the loop are aliases (`R3`, `T2`) handed out by this run, so an id the model did
  not receive (hallucinated or injected) cannot resolve.

## Alternatives

- **Fully agentic loop with all seven tools.** Rejected for the reasons above; worth
  revisiting only for exploratory "investigate this theme" sessions with a human watching.
- **No loop at all** (one structured call returning all proposals). Cheaper, but it cannot
  consult memory selectively or fix a guardrail failure, and the trajectory would show one
  opaque step instead of the judgement we want to evaluate.
