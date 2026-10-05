/**
 * Evaluation and cost numbers shown on the landing page, copied from the
 * published `docs/evals.md` and `docs/costs.md` (written by `uv run evals`).
 *
 * `status: "sample"` marks rows that are still the pre-launch sample from the
 * spec's targets; flip each to "measured" (and update the value) when the eval
 * run publishes it. The page shows the status next to every number.
 */

export type EvalRow = {
  metric: string;
  value: string;
  target: string;
  detail: string;
  status: "measured" | "sample";
};

export const EVALS_SOURCE = "docs/evals.md";
export const EVALS_UPDATED = "2026-10-04";

export const evalRows: EvalRow[] = [
  { metric: "Unapproved writes", value: "0", target: "0", detail: "trajectory rule over the recorded live run (11 tool calls)", status: "measured" },
  { metric: "Evidence ids that exist", value: "100%", target: "100%", detail: "every proposal's quotes resolve to stored reviews (live run)", status: "measured" },
  { metric: "Extraction accuracy (category)", value: "0.93", target: "≥ 0.85", detail: "30 labelled public reviews, live run (sentiment 0.97, device info 1.00)", status: "measured" },
  { metric: "Cluster purity", value: "0.84", target: "≥ 0.80", detail: "60 hand-labelled reviews", status: "sample" },
  { metric: "Reply guardrail pass rate", value: "100%", target: "≥ 95%", detail: "4 drafts in the live run: length, banned phrases, URLs, dates", status: "measured" },
  { metric: "Issue template completeness", value: "100%", target: "100%", detail: "1 issue in the live run: title, summary, evidence, versions, severity", status: "measured" },
];

export type CostRow = { scenario: string; tokens: string; usd: string; steps: string; status: "measured" | "sample" };

export const costRows: CostRow[] = [
  { scenario: "Live run, 30 reviews (5 Oct 2026)", tokens: "30,004", usd: "$0.0065", steps: "11", status: "measured" },
  { scenario: "Quiet day, < 5 new reviews", tokens: "≈ 3.6k", usd: "$0.0007", steps: "11", status: "sample" },
  { scenario: "One app for a month (30 daily runs)", tokens: "≈ 720k", usd: "$0.24", steps: "—", status: "sample" },
];

export const RUN_BUDGET_USD = 0.1;
export const MONTHLY_TARGET_USD = 1;
