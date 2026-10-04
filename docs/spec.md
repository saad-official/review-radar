# Review Radar — product and technical spec

Status: approved 2026-10-04. Level 3 project of the [AI Engineering Journey](https://github.com/saad-official/ai-engineering-journey) (Phase 3: agents and tool use), app 9 of the Vibe Build Series. The 12-question evaluation lives in the journey's `PROJECTS.md`; this file is the build contract.

## 1. Problem

Small mobile teams cannot keep up with app-store reviews. The signal (a crash on one device family, a top feature request, a billing complaint) is buried in noise, and replies are late or absent. Dashboards show charts; nobody turns reviews into tickets and answers.

## 2. Users

Indie developers and small mobile teams; product managers of mobile apps. Demo: any public iOS app id (App Store RSS, about 500 recent reviews per country) and, optionally, a Google Play package id through the Node scraper or CSV import.

## 3. What it does (MVP)

Add an app. The agent fetches new reviews, extracts structured signals from each (category bug|request|praise|billing|performance|other, sentiment, severity, device/OS/app-version mentions, quotes), clusters related reviews into themes with embeddings, drafts a policy-compliant reply per review that warrants one, and proposes GitHub issues for themes that look like bugs or strong requests. **Every write action is a proposal**: replies and issues wait in an approval queue with the evidence (source reviews) and the agent's reasoning; approved issues are created in a connected GitHub repo; approved replies are exported (store APIs need developer credentials, so posting is out of MVP). The agent remembers what was already triaged so a theme is not re-proposed. Runs on a schedule or on demand; every run leaves a trajectory the UI can replay.

Not in MVP: posting replies to the stores, multi-tenant accounts and billing, Jira, Slack, custom policies beyond a text box.

## 4. Agent architecture

```
scheduler (Vercel daily cron / GitHub Actions / "Run now")
  -> POST /api/apps/{id}/runs (creates run) -> POST /api/runs/{id}/process (≤250 s, resumable)
      agent loop (llm_kit.call_tools, max_iterations 24, deadline 200 s, Ledger max_usd 0.10):
        tools: fetch_reviews(store, app, since) · extract_signals(review_ids) · search_memory(query)
               · cluster_reviews(review_ids) · draft_reply(review_id) · propose_issue(theme_id)
               · summarise_run()
        every tool call and result is appended to run_steps (the trajectory)
  -> approval queue (UI) -> executors: create_github_issue (on approval), export_replies (CSV/clipboard)
```

- **Workflow first, agency second.** The run is a deterministic workflow (fetch → extract → cluster → propose) with the model deciding *what* to propose and *how to phrase it*, never *whether to write*. The tool loop exists for the judgement steps (which themes deserve issues, which reviews deserve replies, when to search memory); budgets and the deadline bound it. This is recorded as a decision (`0001-workflow-over-agency.md`).
- **Extraction**: `complete_structured(ReviewSignals)` on the cheap tier in batches of 10 reviews; required-nullable fields; store-provided fields (rating, version) are facts, model fields are labelled as such.
- **Clustering**: embeddings (Gemini `gemini-embedding-001` or Voyage) + agglomerative clustering over cosine distance with a threshold; themes get a model-written title and summary constrained to quote from member reviews; memory links new reviews to existing themes before creating new ones.
- **Memory**: `themes` and `proposals` are the long-term memory; `search_memory` does hybrid search over past themes, proposals and decisions ("declined: duplicate of #42") so the agent respects earlier human decisions.
- **Drafting**: replies follow a policy block (tone, no promises of dates, no refunds offered, no personal data, under 350 characters for App Store), checked by deterministic guardrails (length, banned phrases, no URLs unless allowed, no mention of unreleased features) before entering the queue; issues use a template (title, summary, evidence quotes with review ids and dates, suspected area, affected versions/devices, severity).
- **Security**: review text is untrusted; it is wrapped in tags inside every prompt with an explicit instruction to ignore instructions in it; tool arguments are validated with Pydantic; the agent cannot call executors (issues are created only by the approval endpoint acting on a stored proposal); a run's budget stops it.
- **Trajectory evals**: recorded runs over a fixed review set with scorers: no unapproved writes, every proposal has evidence ids that exist, extraction accuracy against 200 labelled reviews, cluster purity against hand labels, reply guardrail pass rate, cost and step count per run.

## 5. Data model (Neon, schema `review_radar`)

```
apps(id, store: ios|android, store_id, name, country, github_repo, policy text, created_at)
reviews(id, app_id, store_review_id unique, author, rating, title, body, app_version, date, fetched_at, embedding vector(DIM))
signals(review_id pk, category, sentiment, severity 1-5, devices text[], os_versions text[], quotes text[], model, prompt_version)
themes(id, app_id, title, summary, kind: bug|request|praise|billing|other, status: open|resolved|ignored, review_count, embedding, created_at, updated_at)
theme_reviews(theme_id, review_id)
proposals(id, app_id, run_id, kind: reply|issue, theme_id null, review_id null, draft jsonb, reasoning, guardrails jsonb,
          status: proposed|approved|rejected|executed|failed, decided_by, decided_at, result jsonb (issue url))
runs(id, app_id, status, started_at, finished_at, budget jsonb, usage jsonb, summary, error)
run_steps(run_id, seq, at, kind: tool_call|tool_result|model|note, name, args jsonb, result jsonb, usage jsonb)  -- append-only
agent_events(...)  -- append-only audit as in the other apps
```

## 6. API (FastAPI, `/api`)

`POST /api/apps` (store, store_id, country, github_repo?, policy?), `GET /api/apps`, `GET /api/apps/{id}` (counts, last run), `POST /api/apps/{id}/runs` → 202 run, `POST /api/runs/{id}/process` (idempotent, resumable), `GET /api/runs/{id}` + `GET /api/runs/{id}/steps` (trajectory), `GET /api/apps/{id}/themes`, `GET /api/apps/{id}/reviews?theme=`, `GET /api/apps/{id}/proposals?status=`, `POST /api/proposals/{id}/approve` (optional edited draft) → executes issue creation when kind=issue (GitHub token from env or per-app secret stored encrypted), `POST /api/proposals/{id}/reject` (reason stored in memory), `GET /api/proposals/export.csv?app=`, `POST /api/apps/{id}/import` (CSV of reviews), `GET /api/cron/daily` (bearer), `GET /api/health`. SSE `GET /api/runs/{id}/events` for live runs. No accounts in MVP: a single operator password (`OPERATOR_TOKEN`) protects write routes; read routes for the demo app are public.

## 7. Evaluation (`evals/`)

Labelled set: 200 real public reviews (fetched once, stored as fixtures with ids only from the public feed; no extra personal data) with category, sentiment, has-device-info labels; cluster labels for 60 of them; a recorded agent run. Scorers: extraction accuracy (per field), cluster purity/completeness, trajectory rules (no unapproved writes, evidence ids exist, budget respected, step count), reply guardrail pass rate, issue template completeness; LLM-judge rubric for reply tone. `uv run evals` writes `docs/evals.md`.

## 8. Costs (`docs/costs.md`)

A daily run over ~50 new reviews: 5 extraction calls, 1 clustering embed batch, up to ~10 drafts: a few cents at paid rates; measured by the ledger per run and shown in the UI. Target under $1 per app per month.

## 9. Hosting (free)

API on Vercel Python runtime (root `main.py`, framework `fastapi`), runs processed inside `/process` requests (≤250 s) triggered by the UI, QStash, or the daily cron; web on Vercel (`web/`); Neon `review-radar` with pgvector; GitHub issues via a fine-grained PAT (issues: write on one demo repo `saad-official/review-radar-demo-issues`). Google Play ingestion through a tiny Node script run in GitHub Actions (optional) or CSV import.

## 10. Identity

Ops-room calm: **Space Grotesk** (headings), **Inter** (body), **DM Mono** (ids, counts, trajectory). Palette: charcoal on cool grey-white, **radar green** for approved/executed, **amber** for proposed, **rose** for rejected and severity; themes shown as a ranked board with review counts and sparklines of sentiment; the trajectory viewer reads like a flight recorder (step, tool, args, result, tokens). Dark mode via tokens.

## 11. Learning artefacts

Concept notes: agent-loop, tool-design, planning-vs-workflows, agent-memory, human-in-the-loop, durable-execution-and-checkpoints, agent-evaluation, agent-security, background-jobs-and-queues. Decisions: `0001-workflow-over-agency.md`, `0002-memory-as-tables.md`, `0003-approval-gate.md`, `0004-trajectory-evals.md`. README "explain it back" questions (why the agent cannot write, why budgets and deadlines, why memory is just tables, how a prompt injection in a review is contained).

## 12. Tests

Feed parser (RSS JSON and XML fixtures, pagination stop at 400), CSV import, extraction schema and batch splitting, clustering math (threshold, merge with existing themes), guardrails (length, banned phrases, URLs), issue template rendering, approval state machine (idempotent approve/execute, reject reason stored), tool argument validation, trajectory recording, API with fake LLM and fake GitHub; eval scorers on fixtures.
