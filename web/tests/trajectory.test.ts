import { describe, expect, it } from "vitest";
import { RunStepSchema } from "@/lib/api";
import { cumulativeTokens, offsetsFromStart, prettyJson, stepError, stepHeadline, stepTone, summariseValue } from "@/lib/trajectory";

const step = (raw: Record<string, unknown>) => RunStepSchema.parse({ seq: 1, ...raw });

describe("trajectory", () => {
  it("summarises args into one line, collapsing id lists", () => {
    const ids = Array.from({ length: 23 }, (_, i) => `rev_${i}`);
    expect(summariseValue({ store: "ios", app: "6450012345", review_ids: ids })).toBe("store=ios, app=6450012345, review_ids=[23 ids]");
    expect(summariseValue({ review_ids: ["a", "b"] })).toBe("review_ids=[a, b]");
    expect(summariseValue({ query: "crash on launch" })).toBe('query="crash on launch"');
    expect(summariseValue({ categories: { bug: 12, request: 5 } })).toBe("categories={bug:12 request:5}");
    expect(summariseValue(undefined)).toBe("");
    expect(summariseValue("x".repeat(200), 20)).toHaveLength(20);
  });

  it("picks args for calls, results for results, text for notes", () => {
    expect(stepHeadline(step({ kind: "tool_call", name: "fetch_reviews", args: { since: "2026-10-03" } }))).toBe("since=2026-10-03");
    expect(stepHeadline(step({ kind: "tool_result", name: "fetch_reviews", result: { new: 23 } }))).toBe("new=23");
    expect(stepHeadline(step({ kind: "note", result: { text: "run started" } }))).toBe("run started");
  });

  it("detects errors and tones", () => {
    const failed = step({ kind: "tool_result", result: { error: "503 twice" } });
    expect(stepError(failed)).toBe("503 twice");
    expect(stepTone(failed)).toBe("error");
    expect(stepError(step({ kind: "tool_result", result: { ok: false } }))).toBe("tool reported failure");
    expect(stepTone(step({ kind: "model" }))).toBe("model");
  });

  it("accumulates tokens and offsets", () => {
    const steps = [
      step({ kind: "note", at: "2026-10-04T06:00:00Z" }),
      step({ kind: "model", at: "2026-10-04T06:00:02.5Z", usage: { prompt_tokens: 100, completion_tokens: 20, usd: 0.0001 } }),
      step({ kind: "model", usage: { prompt_tokens: 10, completion_tokens: 0 } }),
    ];
    expect(cumulativeTokens(steps)).toEqual([0, 120, 130]);
    expect(offsetsFromStart(steps)).toEqual([0, 2500, undefined]);
  });

  it("pretty-prints JSON safely", () => {
    expect(prettyJson({ a: 1 })).toBe('{\n  "a": 1\n}');
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(prettyJson(cyclic)).toBe("[object Object]");
  });
});
