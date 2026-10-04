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
  { metric: "Unapproved writes", value: "0", target: "0", detail: "trajectory rule over every recorded run", status: "sample" },
  { metric: "Evidence ids that exist", value: "100%", target: "100%", detail: "every proposal's quotes resolve to stored reviews", status: "sample" },
  { metric: "Extraction accuracy (category)", value: "0.91", target: "≥ 0.85", detail: "200 labelled public reviews", status: "sample" },
  { metric: "Cluster purity", value: "0.84", target: "≥ 0.80", detail: "60 hand-labelled reviews", status: "sample" },
  { metric: "Reply guardrail pass rate", value: "97%", target: "≥ 95%", detail: "length, banned phrases, URLs, dates", status: "sample" },
  { metric: "Issue template completeness", value: "100%", target: "100%", detail: "title, summary, evidence, versions, severity", status: "sample" },
];

export type CostRow = { scenario: string; tokens: string; usd: string; steps: string; status: "measured" | "sample" };

export const costRows: CostRow[] = [
  { scenario: "Daily run, ~50 new reviews", tokens: "≈ 24k", usd: "$0.008", steps: "18", status: "sample" },
  { scenario: "Quiet day, < 5 new reviews", tokens: "≈ 3.6k", usd: "$0.0007", steps: "11", status: "sample" },
  { scenario: "One app for a month (30 daily runs)", tokens: "≈ 720k", usd: "$0.24", steps: "—", status: "sample" },
];

export const RUN_BUDGET_USD = 0.1;
export const MONTHLY_TARGET_USD = 1;
