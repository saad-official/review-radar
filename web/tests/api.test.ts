import { describe, expect, it } from "vitest";
import {
  AppSchema,
  ProposalSchema,
  ReviewSchema,
  RunSchema,
  RunStepSchema,
  ThemeSchema,
  eventStatus,
  isResumable,
  isTerminal,
  listOf,
  mergeEvent,
  parseRunEvent,
  type RunEvent,
} from "@/lib/api";
import { exampleProposals, exampleReviews, exampleRunPayload, exampleStepsForLanding, exampleThemes } from "@/lib/mock";

const ev = (seq: number, kind = "note", data: Record<string, unknown> = {}): RunEvent => ({ seq, kind, message: "", data, at: undefined });

describe("fixtures go through the contract", () => {
  it("parses every mock theme, review, proposal and step", () => {
    expect(exampleThemes.map((t) => ThemeSchema.parse(t).kind)).toEqual(["bug", "performance", "request"]);
    expect(exampleReviews.every((r) => ReviewSchema.safeParse(r).success)).toBe(true);
    const proposals = exampleProposals.map((p) => ProposalSchema.parse(p));
    expect(proposals).toHaveLength(5);
    expect(proposals.filter((p) => p.kind === "issue")).toHaveLength(2);
    expect(proposals.every((p) => p.status === "proposed" && p.guardrails.passed && p.draft.evidence.length > 0)).toBe(true);
    const steps = exampleStepsForLanding().map((s) => RunStepSchema.parse(s));
    expect(steps.length).toBeGreaterThanOrEqual(14);
    expect(steps.map((s) => s.seq)).toEqual(steps.map((_, i) => i + 1));
  });

  it("evidence ids in proposals exist among the reviews", () => {
    const ids = new Set(exampleReviews.map((r) => r.id));
    for (const p of exampleProposals.map((x) => ProposalSchema.parse(x))) {
      for (const e of p.draft.evidence) expect(ids.has(e.review_id ?? "")).toBe(true);
    }
  });

  it("sums run usage from the steps", () => {
    const run = RunSchema.parse(exampleRunPayload());
    expect(run.status).toBe("done");
    expect(run.usage.max_usd).toBe(0.1);
    expect(run.usage.total_tokens).toBe(run.usage.prompt_tokens + run.usage.completion_tokens);
    expect(run.usage.usd).toBeGreaterThan(0);
    expect(run.usage.usd).toBeLessThan(0.1);
  });
});

describe("tolerant parsing", () => {
  it("fills an app from a minimal payload", () => {
    const a = AppSchema.parse({ id: 7, store_id: "123456" });
    expect(a).toMatchObject({ id: "7", store: "ios", country: "us", review_count: 0, proposals_waiting: 0, name: "App Store 123456" });
    expect(a.github_repo).toBeUndefined();
    expect(a.last_run).toBeUndefined();
  });

  it("takes last_run_at from last_run and ignores junk counts", () => {
    const a = AppSchema.parse({
      id: "a",
      store: "ANDROID",
      review_count: "lots",
      theme_count: -2,
      last_run: { id: 9, status: "done", started_at: "2026-10-04T06:00:00Z" },
    });
    expect(a.store).toBe("android");
    expect(a.review_count).toBe(0);
    expect(a.theme_count).toBe(0);
    expect(a.last_run_at).toBe("2026-10-04T06:00:00Z");
    expect(a.last_run?.id).toBe("9");
  });

  it("falls back on unknown enums and null usage", () => {
    const r = RunSchema.parse({ id: 1, status: "teleporting", usage: null, summary: { text: "ok" } });
    expect(r.status).toBe("queued");
    expect(r.usage.usd).toBe(0);
    expect(r.summary).toBe("ok");
    const s = RunStepSchema.parse({ seq: "3", kind: "thinking", name: null });
    expect(s).toMatchObject({ seq: 3, kind: "note", name: "" });
  });

  it("clamps sentiment and drops non-numbers", () => {
    const t = ThemeSchema.parse({ id: "t", title: "  ", sentiment: [0, 3, "4", "x", 9, null] });
    expect(t.title).toBe("Untitled theme");
    expect(t.sentiment).toEqual([1, 3, 4, 5]);
  });

  it("normalises review signals", () => {
    const r = ReviewSchema.parse({ id: 1, rating: 4.6, body: null, signals: { category: "Bug", sentiment: -0.8, severity: 9, devices: ["iPhone", 12, null] } });
    expect(r.rating).toBe(5);
    expect(r.body).toBe("");
    expect(r.signals).toMatchObject({ category: "bug", sentiment: "negative", severity: 5, devices: ["iPhone", "12"], quotes: [] });
    expect(ReviewSchema.parse({ id: 2, signals: "nope" }).signals).toBeNull();
  });

  it("infers proposal kind, guardrail pass and missing draft", () => {
    const issue = ProposalSchema.parse({ id: 1, draft: { title: "Crash", evidence: ["a quote", { quote: "" }, { review_id: 5, quote: "b" }] } });
    expect(issue.kind).toBe("issue");
    expect(issue.draft.evidence).toEqual([
      { review_id: undefined, quote: "a quote", date: undefined },
      { review_id: "5", quote: "b", date: undefined },
    ]);
    expect(issue.guardrails).toEqual({ passed: true, checks: [] });
    const failed = ProposalSchema.parse({ id: 2, kind: "reply", guardrails: { checks: [{ name: "length", ok: false }] }, result: null });
    expect(failed.guardrails.passed).toBe(false);
    expect(failed.result).toEqual({});
    expect(ProposalSchema.parse({ id: 3, draft: null }).kind).toBe("reply");
  });

  it("reads bare arrays and envelopes, dropping bad rows", () => {
    expect(listOf(ThemeSchema, [{ id: "a" }, { nope: true }, { id: "b" }]).map((t) => t.id)).toEqual(["a", "b"]);
    expect(listOf(ThemeSchema, { items: [{ id: 1 }] })).toHaveLength(1);
    expect(listOf(ThemeSchema, { themes: [{ id: 1 }] }, "themes")).toHaveLength(1);
    expect(listOf(ThemeSchema, "garbage")).toEqual([]);
  });
});

describe("events and processing", () => {
  it("parses SSE payloads and ignores keep-alives and junk", () => {
    const raw = JSON.stringify({ seq: 3, at: "2026-10-04T06:00:00Z", kind: "tool_call", message: "fetch_reviews", data: { status: "fetching" } });
    expect(parseRunEvent(raw)).toMatchObject({ seq: 3, kind: "tool_call", data: { status: "fetching" } });
    expect(parseRunEvent("")).toBeNull();
    expect(parseRunEvent("not json")).toBeNull();
    expect(parseRunEvent("[1]")).toBeNull();
    expect(parseRunEvent(JSON.stringify({ kind: "note", message: "x" }), 9)?.seq).toBe(9);
    expect(parseRunEvent(JSON.stringify({ seq: 1, data: null }))?.data).toEqual({});
  });

  it("derives status and terminal state", () => {
    expect(eventStatus(ev(1, "tool_call", { status: "extracting" }))).toBe("extracting");
    expect(eventStatus(ev(1, "done"))).toBe("done");
    expect(eventStatus(ev(1, "error"))).toBe("failed");
    expect(eventStatus(ev(1, "note", { status: "bogus" }))).toBeUndefined();
    expect(isTerminal("done")).toBe(true);
    expect(isTerminal("proposing")).toBe(false);
  });

  it("merges events by seq, in order, without duplicates", () => {
    let list: RunEvent[] = [];
    for (const s of [2, 1, 2, 3]) list = mergeEvent(list, ev(s));
    expect(list.map((e) => e.seq)).toEqual([1, 2, 3]);
  });

  it("recognises the resumable 202 body", () => {
    expect(isResumable({ resumable: true })).toBe(true);
    expect(isResumable({ resumable: "yes" })).toBe(false);
    expect(isResumable(null)).toBe(false);
  });
});
