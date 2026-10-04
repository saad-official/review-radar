/**
 * Errors from the Review Radar API, normalised from whatever JSON shape the
 * server sent. The contract (spec §6) is `{ detail: { code, message, retry_after? } }`
 * with codes `unauthorized` (401), `rate_limited` (429), `not_found` (404) and
 * `invalid_input` (400/422). FastAPI's defaults, `{ detail: string }` and
 * `{ detail: [{ loc, msg, type }] }`, are read too, as are flat `{ code, message }`
 * and `{ error: { code, message } }` bodies.
 */

export type ApiErrorCode =
  | "unauthorized"
  | "rate_limited"
  | "not_found"
  | "invalid_input"
  | "conflict"
  | "budget_exceeded"
  | "network"
  | "server"
  | "unknown";

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  /** Seconds until a rate-limited client may retry, when the server said so. */
  readonly retryAfter?: number;
  /** Field-level messages from request validation, keyed by body field. */
  readonly fields: Record<string, string>;

  constructor(opts: { status: number; code: ApiErrorCode; message: string; retryAfter?: number; fields?: Record<string, string> }) {
    super(opts.message);
    this.name = "ApiError";
    this.status = opts.status;
    this.code = opts.code;
    this.retryAfter = opts.retryAfter;
    this.fields = opts.fields ?? {};
  }
}

const KNOWN_CODES = new Set<ApiErrorCode>(["unauthorized", "rate_limited", "not_found", "invalid_input", "conflict", "budget_exceeded"]);

/** Aliases the API might send for the same meaning. */
const CODE_ALIASES: Record<string, ApiErrorCode> = {
  validation: "invalid_input",
  validation_error: "invalid_input",
  bad_request: "invalid_input",
  forbidden: "unauthorized",
  app_not_found: "not_found",
  run_not_found: "not_found",
  proposal_not_found: "not_found",
};

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.trim() !== "" ? v : undefined;
}

function num(v: unknown): number | undefined {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return undefined;
}

function readCode(raw: unknown): ApiErrorCode | undefined {
  const c = str(raw);
  if (!c) return undefined;
  if (KNOWN_CODES.has(c as ApiErrorCode)) return c as ApiErrorCode;
  return CODE_ALIASES[c];
}

/** Guess a code from the status when the server did not send one. */
export function codeFromStatus(status: number): ApiErrorCode {
  if (status === 401 || status === 403) return "unauthorized";
  if (status === 429) return "rate_limited";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 400 || status === 422) return "invalid_input";
  if (status >= 500) return "server";
  return "unknown";
}

function defaultMessage(code: ApiErrorCode, status: number): string {
  switch (code) {
    case "unauthorized":
      return "The operator token is missing or was not accepted. Add it in Operator settings.";
    case "rate_limited":
      return "Too many requests. Wait a moment and try again.";
    case "not_found":
      return "That item does not exist, or it was removed.";
    case "invalid_input":
      return "Some of the input was not accepted. Check the form.";
    case "conflict":
      return "Someone (or another tab) already acted on this.";
    case "budget_exceeded":
      return "The run hit its cost ceiling before finishing.";
    case "server":
      return `The API returned an error (${status}). Try again in a minute.`;
    default:
      return `Request failed (${status}).`;
  }
}

/** Build an ApiError from a status, a parsed (or unparsable) body and an optional Retry-After header. */
export function parseApiError(status: number, body: unknown, retryAfterHeader?: string | null): ApiError {
  let code: ApiErrorCode | undefined;
  let message: string | undefined;
  let retryAfter = num(retryAfterHeader ?? undefined);
  const fields: Record<string, string> = {};

  const readObject = (o: Record<string, unknown>) => {
    code = readCode(o.code) ?? code;
    message = str(o.message) ?? str(o.msg) ?? str(o.detail) ?? message;
    retryAfter = num(o.retry_after) ?? num(o.retryAfter) ?? retryAfter;
    const f = str(o.field);
    if (f && message) fields[f] = message;
  };

  if (isRecord(body)) {
    const detail = body.detail;
    if (typeof detail === "string") {
      message = detail;
    } else if (Array.isArray(detail)) {
      // FastAPI request validation: [{ loc: ["body", "store_id"], msg: "Field required" }]
      const msgs: string[] = [];
      for (const d of detail) {
        if (!isRecord(d)) continue;
        const msg = str(d.msg) ?? "Invalid value";
        const loc = Array.isArray(d.loc) ? d.loc.map(String).filter((l) => l !== "body" && l !== "query") : [];
        const field = loc[loc.length - 1];
        if (field && !fields[field]) fields[field] = msg;
        msgs.push(field ? `${field}: ${msg}` : msg);
      }
      message = msgs.length > 0 ? msgs.join("; ") : undefined;
      code = "invalid_input";
    } else if (isRecord(detail)) {
      readObject(detail);
    } else if (isRecord(body.error)) {
      readObject(body.error);
    } else {
      readObject(body);
    }
  } else if (typeof body === "string" && body.trim() !== "" && !body.trim().startsWith("<")) {
    message = body.trim().slice(0, 300);
  }

  const resolved = code ?? codeFromStatus(status);
  return new ApiError({ status, code: resolved, message: message ?? defaultMessage(resolved, status), retryAfter, fields });
}

export function networkError(apiUrl: string): ApiError {
  return new ApiError({
    status: 0,
    code: "network",
    message: `Could not reach the API at ${apiUrl}. It may be waking up (free hosting sleeps); try again in a few seconds.`,
  });
}

export function asApiError(e: unknown): ApiError {
  return e instanceof ApiError
    ? e
    : new ApiError({ status: 0, code: "unknown", message: e instanceof Error ? e.message : String(e) });
}

/** Short heading for an error box or toast. */
export function errorTitle(err: ApiError): string {
  switch (err.code) {
    case "unauthorized":
      return "Operator token needed";
    case "rate_limited":
      return "Slow down";
    case "not_found":
      return "Not found";
    case "invalid_input":
      return "Check the input";
    case "conflict":
      return "Already handled";
    case "budget_exceeded":
      return "Cost ceiling reached";
    case "network":
      return "API unreachable";
    case "server":
      return "API error";
    default:
      return "Something went wrong";
  }
}
