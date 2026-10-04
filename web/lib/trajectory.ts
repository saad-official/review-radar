/**
 * Flight-recorder helpers: turn a run step's free-form `args` / `result` JSON into
 * one readable line, and pick out the numbers the table shows. All pure.
 */
import type { RunStep, StepKind } from "./api";

const MAX = 96;

function clip(s: string, max = MAX): string {
  const one = s.replace(/\s+/g, " ").trim();
  return one.length > max ? `${one.slice(0, max - 1)}…` : one;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Lists of ids collapse to a count; short lists of scalars are shown inline. */
function summariseArray(key: string, arr: unknown[]): string {
  if (arr.length === 0) return "[]";
  const scalars = arr.every((x) => typeof x === "string" || typeof x === "number");
  const looksLikeIds = /(^|_)ids?$/.test(key) || /_ids$/.test(key);
  if (scalars && (looksLikeIds || arr.length > 3)) {
    if (arr.length <= 2) return `[${arr.join(", ")}]`;
    return `[${arr.length} ${looksLikeIds ? "ids" : "items"}]`;
  }
  if (scalars) return `[${arr.join(", ")}]`;
  return `[${arr.length} ${arr.length === 1 ? "item" : "items"}]`;
}

function summariseScalar(v: unknown): string {
  if (typeof v === "string") return /\s/.test(v) || v.length > 32 ? JSON.stringify(clip(v, 40)) : v;
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(4)));
  if (typeof v === "boolean" || v === null) return String(v);
  return "…";
}

/** `store=ios, app=6450012345, review_ids=[23 ids]` */
export function summariseValue(value: unknown, max = MAX): string {
  if (value === undefined) return "";
  if (typeof value === "string") return clip(value, max);
  if (Array.isArray(value)) return summariseArray("", value);
  if (!isRecord(value)) return summariseScalar(value);
  const parts: string[] = [];
  for (const [k, v] of Object.entries(value)) {
    if (v === undefined) continue;
    if (Array.isArray(v)) parts.push(`${k}=${summariseArray(k, v)}`);
    else if (isRecord(v)) {
      const inner = Object.entries(v)
        .slice(0, 4)
        .map(([ik, iv]) => `${ik}:${Array.isArray(iv) ? summariseArray(ik, iv) : isRecord(iv) ? "{…}" : summariseScalar(iv)}`)
        .join(" ");
      parts.push(`${k}={${inner}${Object.keys(v).length > 4 ? " …" : ""}}`);
    } else parts.push(`${k}=${summariseScalar(v)}`);
  }
  return clip(parts.join(", "), max);
}

/** Pretty JSON for the expanded view; never throws. */
export function prettyJson(value: unknown): string {
  if (value === undefined) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function stepTokens(step: Pick<RunStep, "usage">): number | undefined {
  if (!step.usage) return undefined;
  const t = step.usage.total_tokens;
  return t > 0 ? t : undefined;
}

/** An error in a tool result, if the result says so (`error`, `ok: false`, `status: "error"`). */
export function stepError(step: Pick<RunStep, "result">): string | undefined {
  const r = step.result;
  if (!isRecord(r)) return undefined;
  if (typeof r.error === "string" && r.error.trim() !== "") return r.error;
  if (r.ok === false) return typeof r.message === "string" ? r.message : "tool reported failure";
  if (r.status === "error") return typeof r.message === "string" ? r.message : "tool reported an error";
  return undefined;
}

export type StepTone = "call" | "result" | "model" | "note" | "error";

export function stepTone(step: Pick<RunStep, "kind" | "result">): StepTone {
  if (stepError(step)) return "error";
  const map: Record<StepKind, StepTone> = { tool_call: "call", tool_result: "result", model: "model", note: "note" };
  return map[step.kind];
}

/** The single "what happened" column: the result for results, args for calls, text for notes. */
export function stepHeadline(step: RunStep): string {
  if (step.kind === "note" || step.kind === "model") {
    const r = step.result ?? step.args;
    if (isRecord(r) && typeof r.text === "string") return clip(r.text);
    if (isRecord(r) && typeof r.message === "string") return clip(r.message);
    return summariseValue(r);
  }
  return summariseValue(step.kind === "tool_call" ? step.args : step.result);
}

/** Running total of tokens after each step (for the "cum." column). */
export function cumulativeTokens(steps: Pick<RunStep, "usage">[]): number[] {
  let total = 0;
  return steps.map((s) => (total += stepTokens(s) ?? 0));
}

/** Milliseconds from the first timestamped step. */
export function offsetsFromStart(steps: Pick<RunStep, "at">[]): (number | undefined)[] {
  const first = steps.find((s) => s.at && !Number.isNaN(Date.parse(s.at)))?.at;
  const t0 = first ? Date.parse(first) : undefined;
  return steps.map((s) => {
    if (t0 === undefined || !s.at) return undefined;
    const t = Date.parse(s.at);
    return Number.isNaN(t) ? undefined : t - t0;
  });
}
