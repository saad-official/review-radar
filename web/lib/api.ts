import { z } from "zod";
import { API_MOCK, API_URL } from "./config";
import { ApiError, networkError, parseApiError } from "./errors";
import { readOperatorToken } from "./operator";
import * as mock from "./mock";

/*
  Contract with the FastAPI service (spec §6). Parsing is deliberately
  tolerant: optional fields may be missing or null, numbers may be absent
  while a run is in progress, unknown enum values fall back to a safe
  default, and unknown extra fields are ignored. List endpoints may answer
  with a bare array or an envelope ({ items: [...] }); one malformed row is
  dropped instead of failing the whole list.
*/

/* ------------------------------------------------------------------ */
/* Enums                                                                */

export const STORES = ["ios", "android"] as const;
export type Store = (typeof STORES)[number];

export const RUN_STATUSES = ["queued", "fetching", "extracting", "clustering", "proposing", "done", "failed"] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

export const STEP_KINDS = ["tool_call", "tool_result", "model", "note"] as const;
export type StepKind = (typeof STEP_KINDS)[number];

export const THEME_KINDS = ["bug", "request", "praise", "billing", "performance", "other"] as const;
export type ThemeKind = (typeof THEME_KINDS)[number];

export const THEME_STATUSES = ["open", "resolved", "ignored"] as const;
export type ThemeStatus = (typeof THEME_STATUSES)[number];

export const CATEGORIES = ["bug", "request", "praise", "billing", "performance", "other"] as const;
export type Category = (typeof CATEGORIES)[number];

export const PROPOSAL_KINDS = ["reply", "issue"] as const;
export type ProposalKind = (typeof PROPOSAL_KINDS)[number];

export const PROPOSAL_STATUSES = ["proposed", "approved", "rejected", "executed", "failed"] as const;
export type ProposalStatus = (typeof PROPOSAL_STATUSES)[number];

export function isTerminal(status: string | undefined): status is "done" | "failed" {
  return status === "done" || status === "failed";
}

/* ------------------------------------------------------------------ */
/* Tolerant building blocks                                             */

function oneOf<T extends string>(values: readonly T[], fallback: T) {
  return z
    .unknown()
    .optional()
    .transform((v): T => (typeof v === "string" && (values as readonly string[]).includes(v.toLowerCase()) ? (v.toLowerCase() as T) : fallback));
}

const idSchema = z.union([z.string(), z.number()]).transform(String);
const optId = z
  .union([z.string(), z.number()])
  .nullish()
  .catch(null)
  .transform((v) => (v === null || v === undefined || v === "" ? undefined : String(v)));
const count = z.coerce.number().nonnegative().nullish().catch(null).transform((v) => (Number.isFinite(v) ? (v as number) : 0));
const optNum = z.coerce.number().nullish().catch(null).transform((v) => (v === null || v === undefined || !Number.isFinite(v) ? undefined : v));
const optText = z.string().nullish().catch(null).transform((v) => (v === null || v === undefined || v.trim() === "" ? undefined : v));
const text = z.string().nullish().catch(null).transform((v) => v ?? "");
const textList = z
  .array(z.unknown())
  .nullish()
  .catch(null)
  .transform((l) => (l ?? []).flatMap((x) => (typeof x === "string" && x.trim() !== "" ? [x] : typeof x === "number" ? [String(x)] : [])));
const record = z
  .record(z.string(), z.unknown())
  .nullish()
  .catch(null)
  .transform((d) => d ?? {});

/** A summary may arrive as a string or as structured JSON; the UI wants text. */
const summaryText = z
  .unknown()
  .optional()
  .transform((v): string | undefined => {
    if (typeof v === "string") return v.trim() === "" ? undefined : v;
    if (v && typeof v === "object") {
      const o = v as Record<string, unknown>;
      if (typeof o.text === "string") return o.text;
      if (typeof o.summary === "string") return o.summary;
      return JSON.stringify(v);
    }
    return undefined;
  });

/** Parse a list endpoint: a bare array, or `{ items | data | results | <key>: [...] }`. */
export function listOf<S extends z.ZodType>(schema: S, raw: unknown, key?: string): z.output<S>[] {
  let rows: unknown[] = [];
  if (Array.isArray(raw)) rows = raw;
  else if (raw && typeof raw === "object") {
    const o = raw as Record<string, unknown>;
    const candidate = (key ? o[key] : undefined) ?? o.items ?? o.data ?? o.results;
    if (Array.isArray(candidate)) rows = candidate;
  }
  return rows.flatMap((r) => {
    const p = schema.safeParse(r);
    return p.success ? [p.data as z.output<S>] : [];
  });
}

/* ------------------------------------------------------------------ */
/* Schemas                                                              */

export const UsageSchema = z
  .object({
    prompt_tokens: count,
    completion_tokens: count,
    total_tokens: optNum,
    usd: count,
    max_usd: optNum,
  })
  .transform((u) => ({ ...u, total_tokens: u.total_tokens ?? u.prompt_tokens + u.completion_tokens }));
export type Usage = z.infer<typeof UsageSchema>;
export const EMPTY_USAGE: Usage = { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, usd: 0, max_usd: undefined };
const usage = UsageSchema.nullish().catch(null).transform((u) => u ?? EMPTY_USAGE);
const optUsage = UsageSchema.nullish().catch(null).transform((u) => u ?? undefined);

const LastRunSchema = z.object({ id: idSchema, status: oneOf(RUN_STATUSES, "queued"), started_at: optText, finished_at: optText });

export const AppSchema = z
  .object({
    id: idSchema,
    store: oneOf(STORES, "ios"),
    store_id: text,
    name: optText,
    country: optText,
    github_repo: optText,
    policy: optText,
    review_count: count,
    theme_count: count,
    proposals_waiting: count,
    /** Optional: reviews fetched by the latest run. Not in spec §6 yet. */
    new_reviews: optNum,
    last_run_at: optText,
    last_run: LastRunSchema.nullish().catch(null).transform((v) => v ?? undefined),
  })
  .transform((a) => ({
    ...a,
    name: a.name ?? (a.store_id ? `${a.store === "ios" ? "App Store" : "Play"} ${a.store_id}` : "Untitled app"),
    country: a.country ?? "us",
    last_run_at: a.last_run_at ?? a.last_run?.started_at,
  }));
export type App = z.infer<typeof AppSchema>;

export const RunSchema = z.object({
  id: idSchema,
  app_id: optId,
  status: oneOf(RUN_STATUSES, "queued"),
  started_at: optText,
  finished_at: optText,
  usage,
  step_count: count,
  summary: summaryText,
  error: optText,
});
export type Run = z.infer<typeof RunSchema>;

export const RunStepSchema = z.object({
  seq: z.coerce.number().catch(0),
  at: optText,
  kind: oneOf(STEP_KINDS, "note"),
  name: text,
  args: z.unknown().optional(),
  result: z.unknown().optional(),
  usage: optUsage,
});
export type RunStep = z.infer<typeof RunStepSchema>;

const rating = z.coerce
  .number()
  .nullish()
  .catch(null)
  .transform((v) => (v === null || v === undefined || !Number.isFinite(v) ? 0 : Math.min(5, Math.max(0, Math.round(v)))));

export const ThemeSchema = z.object({
  id: idSchema,
  title: z.string().nullish().catch(null).transform((v) => v?.trim() || "Untitled theme"),
  summary: optText,
  kind: oneOf(THEME_KINDS, "other"),
  status: oneOf(THEME_STATUSES, "open"),
  review_count: count,
  sentiment: z
    .array(z.unknown())
    .nullish()
    .catch(null)
    .transform((l) =>
      (l ?? []).flatMap((x) => {
        const n = typeof x === "number" ? x : typeof x === "string" ? Number(x) : NaN;
        return Number.isFinite(n) ? [Math.min(5, Math.max(1, n))] : [];
      }),
    ),
  updated_at: optText,
});
export type Theme = z.infer<typeof ThemeSchema>;

/** Sentiment may be a label or a score in [-1, 1]. Normalised to a label. */
const sentimentLabel = z.unknown().optional().transform((v): "negative" | "neutral" | "positive" | "mixed" | undefined => {
  if (typeof v === "string") {
    const s = v.toLowerCase();
    if (s === "negative" || s === "neutral" || s === "positive" || s === "mixed") return s;
    return undefined;
  }
  if (typeof v === "number" && Number.isFinite(v)) return v < -0.2 ? "negative" : v > 0.2 ? "positive" : "neutral";
  return undefined;
});

export const SignalsSchema = z.object({
  category: oneOf(CATEGORIES, "other"),
  sentiment: sentimentLabel,
  severity: optNum.transform((v) => (v === undefined ? undefined : Math.min(5, Math.max(1, Math.round(v))))),
  devices: textList,
  os_versions: textList,
  quotes: textList,
});
export type Signals = z.infer<typeof SignalsSchema>;

export const ReviewSchema = z.object({
  id: idSchema,
  store_review_id: optText,
  author: optText,
  rating,
  title: optText,
  body: text,
  app_version: optText,
  date: optText,
  signals: SignalsSchema.nullish().catch(null).transform((v) => v ?? null),
});
export type Review = z.infer<typeof ReviewSchema>;

export const EvidenceSchema = z.object({
  review_id: optId,
  quote: text,
  date: optText,
});
export type Evidence = z.infer<typeof EvidenceSchema>;

export const DraftSchema = z.object({
  body: optText,
  title: optText,
  summary: optText,
  evidence: z
    .array(z.unknown())
    .nullish()
    .catch(null)
    .transform((l) =>
      (l ?? []).flatMap((e) => {
        if (typeof e === "string") return [{ review_id: undefined, quote: e, date: undefined }];
        const p = EvidenceSchema.safeParse(e);
        return p.success && p.data.quote !== "" ? [p.data] : [];
      }),
    ),
  affected_versions: textList,
  severity: optNum,
  suspected_area: optText,
  devices: textList,
});
export type Draft = z.infer<typeof DraftSchema>;

const CheckSchema = z.object({ name: z.string().catch("check"), ok: z.boolean().catch(false), note: optText });

export const GuardrailsSchema = z
  .object({
    passed: z.boolean().nullish().catch(null),
    checks: z
      .array(z.unknown())
      .nullish()
      .catch(null)
      .transform((l) =>
        (l ?? []).flatMap((c) => {
          const p = CheckSchema.safeParse(c);
          return p.success ? [p.data] : [];
        }),
      ),
  })
  .transform((g) => ({ checks: g.checks, passed: g.passed ?? g.checks.every((c) => c.ok) }));
export type Guardrails = z.infer<typeof GuardrailsSchema>;

export const ProposalSchema = z
  .object({
    id: idSchema,
    kind: z.unknown().optional(),
    status: oneOf(PROPOSAL_STATUSES, "proposed"),
    theme_id: optId,
    review_id: optId,
    run_id: optId,
    draft: DraftSchema.nullish().catch(null),
    reasoning: optText,
    guardrails: GuardrailsSchema.nullish().catch(null),
    decided_at: optText,
    decided_by: optText,
    reason: optText,
    created_at: optText,
    result: z
      .object({ url: optText, number: optNum, error: optText })
      .nullish()
      .catch(null)
      .transform((r): { url?: string; number?: number; error?: string } => r ?? {}),
  })
  .transform((p) => {
    const draft: Draft = p.draft ?? { evidence: [], affected_versions: [], devices: [], body: undefined, title: undefined, summary: undefined, severity: undefined, suspected_area: undefined };
    const kind: ProposalKind =
      p.kind === "reply" || p.kind === "issue" ? p.kind : draft.title && !draft.body ? "issue" : "reply";
    return { ...p, kind, draft, guardrails: p.guardrails ?? { passed: true, checks: [] } };
  });
export type Proposal = z.infer<typeof ProposalSchema>;

export const RunEventSchema = z.object({
  seq: z.coerce.number().catch(0),
  at: optText,
  kind: z.string().catch("note"),
  message: text,
  data: record,
});
export type RunEvent = z.infer<typeof RunEventSchema>;

/** Parse one SSE `data:` payload. Returns null for keep-alives and junk. */
export function parseRunEvent(raw: string, fallbackSeq?: number): RunEvent | null {
  const value = raw.trim();
  if (value === "") return null;
  let json: unknown;
  try {
    json = JSON.parse(value);
  } catch {
    return null;
  }
  if (typeof json !== "object" || json === null || Array.isArray(json)) return null;
  const r = RunEventSchema.safeParse(json);
  if (!r.success) return null;
  if (fallbackSeq !== undefined && !("seq" in json)) return { ...r.data, seq: fallbackSeq };
  return r.data;
}

/** The run status an event implies, if any (from `data.status`, or the done/failed kinds). */
export function eventStatus(e: RunEvent): RunStatus | undefined {
  const s = e.data.status;
  if (typeof s === "string" && (RUN_STATUSES as readonly string[]).includes(s)) return s as RunStatus;
  if (e.kind === "done") return "done";
  if (e.kind === "failed" || e.kind === "error") return "failed";
  return undefined;
}

/* ------------------------------------------------------------------ */
/* Transport                                                            */

export const eventsUrl = (runId: string) => `${API_URL}/api/runs/${encodeURIComponent(runId)}/events`;
export const exportUrl = (appId: string) => `${API_URL}/api/proposals/export.csv?app=${encodeURIComponent(appId)}`;

async function readBody(res: Response): Promise<unknown> {
  const t = await res.text();
  try {
    return JSON.parse(t);
  } catch {
    return t;
  }
}

function authHeader(): Record<string, string> {
  const token = readOperatorToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

type RequestOptions = { method?: string; body?: BodyInit; json?: unknown; write?: boolean };

async function request(path: string, opts: RequestOptions = {}): Promise<{ status: number; body: unknown }> {
  const headers: Record<string, string> = { Accept: "application/json", ...(opts.write ? authHeader() : {}) };
  let body = opts.body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { method: opts.method ?? "GET", headers, body, cache: "no-store" });
  } catch {
    throw networkError(API_URL);
  }
  const parsed = await readBody(res);
  if (!res.ok) throw parseApiError(res.status, parsed, res.headers.get("Retry-After"));
  return { status: res.status, body: parsed };
}

const get = async (path: string) => (await request(path)).body;

function invalid(what: string): ApiError {
  return new ApiError({ status: 502, code: "server", message: `The API sent ${what} this page could not read.` });
}

function one<S extends z.ZodType>(schema: S, raw: unknown, what: string): z.output<S> {
  const p = schema.safeParse(raw);
  if (!p.success) throw invalid(what);
  return p.data as z.output<S>;
}

/** Unwrap `{ app: {...} }`-style envelopes. */
function unwrap(raw: unknown, key: string): unknown {
  if (raw && typeof raw === "object" && !Array.isArray(raw) && key in raw) {
    const inner = (raw as Record<string, unknown>)[key];
    if (inner && typeof inner === "object") return inner;
  }
  return raw;
}

const enc = encodeURIComponent;

/* ------------------------------------------------------------------ */
/* Reads (public)                                                       */

export async function listApps(): Promise<App[]> {
  return listOf(AppSchema, API_MOCK ? await mock.mockListApps() : await get("/api/apps"), "apps");
}

export async function getApp(id: string): Promise<App> {
  return one(AppSchema, unwrap(API_MOCK ? await mock.mockGetApp(id) : await get(`/api/apps/${enc(id)}`), "app"), "an app");
}

/** Not in spec §6: `GET /api/apps/{id}/runs` (newest first). */
export async function listRuns(appId: string): Promise<Run[]> {
  return listOf(RunSchema, API_MOCK ? await mock.mockListRuns(appId) : await get(`/api/apps/${enc(appId)}/runs`), "runs");
}

export async function getRun(id: string): Promise<Run> {
  return one(RunSchema, unwrap(API_MOCK ? await mock.mockGetRun(id) : await get(`/api/runs/${enc(id)}`), "run"), "a run");
}

export async function getRunSteps(id: string): Promise<RunStep[]> {
  const steps = listOf(RunStepSchema, API_MOCK ? await mock.mockGetSteps(id) : await get(`/api/runs/${enc(id)}/steps`), "steps");
  return steps.sort((a, b) => a.seq - b.seq);
}

export async function listThemes(appId: string): Promise<Theme[]> {
  return listOf(ThemeSchema, API_MOCK ? await mock.mockListThemes(appId) : await get(`/api/apps/${enc(appId)}/themes`), "themes");
}

export async function listReviews(appId: string, opts: { theme?: string } = {}): Promise<Review[]> {
  const q = opts.theme ? `?theme=${enc(opts.theme)}` : "";
  return listOf(ReviewSchema, API_MOCK ? await mock.mockListReviews(appId, opts.theme) : await get(`/api/apps/${enc(appId)}/reviews${q}`), "reviews");
}

export async function listProposals(appId: string, opts: { status?: ProposalStatus; run?: string } = {}): Promise<Proposal[]> {
  const params = new URLSearchParams();
  if (opts.status) params.set("status", opts.status);
  if (opts.run) params.set("run", opts.run);
  const q = params.size > 0 ? `?${params.toString()}` : "";
  const raw = API_MOCK ? await mock.mockListProposals(appId, opts) : await get(`/api/apps/${enc(appId)}/proposals${q}`);
  // Filter client-side as well, in case the API ignores a parameter.
  return listOf(ProposalSchema, raw, "proposals").filter(
    (p) => (!opts.status || p.status === opts.status) && (!opts.run || !p.run_id || p.run_id === opts.run),
  );
}

/* ------------------------------------------------------------------ */
/* Writes (operator token)                                              */

export type CreateAppInput = {
  store: Store;
  store_id: string;
  country: string;
  github_repo?: string;
  policy?: string;
};

export async function createApp(input: CreateAppInput): Promise<App> {
  const body: Record<string, string> = { store: input.store, store_id: input.store_id.trim(), country: input.country.trim().toLowerCase() };
  if (input.github_repo?.trim()) body.github_repo = input.github_repo.trim();
  if (input.policy?.trim()) body.policy = input.policy.trim();
  const raw = API_MOCK ? await mock.mockCreateApp(body) : (await request("/api/apps", { method: "POST", json: body, write: true })).body;
  return one(AppSchema, unwrap(raw, "app"), "an app");
}

export type UpdateAppInput = { github_repo?: string | null; policy?: string | null };

/** Not in spec §6: `PATCH /api/apps/{id}` with `{ github_repo?, policy? }`. */
export async function updateApp(id: string, patch: UpdateAppInput): Promise<App> {
  const raw = API_MOCK ? await mock.mockUpdateApp(id, patch) : (await request(`/api/apps/${enc(id)}`, { method: "PATCH", json: patch, write: true })).body;
  return one(AppSchema, unwrap(raw, "app"), "an app");
}

export async function createRun(appId: string): Promise<Run> {
  const raw = API_MOCK ? await mock.mockCreateRun(appId) : (await request(`/api/apps/${enc(appId)}/runs`, { method: "POST", write: true })).body;
  return one(RunSchema, unwrap(raw, "run"), "a run");
}

export async function approveProposal(id: string, edited?: Partial<Pick<Draft, "body" | "title" | "summary">>): Promise<Proposal> {
  const body = edited && Object.keys(edited).length > 0 ? { draft: edited } : {};
  const raw = API_MOCK ? await mock.mockDecide(id, "approve", body) : (await request(`/api/proposals/${enc(id)}/approve`, { method: "POST", json: body, write: true })).body;
  return one(ProposalSchema, unwrap(raw, "proposal"), "a proposal");
}

export async function rejectProposal(id: string, reason: string): Promise<Proposal> {
  const body = { reason: reason.trim() };
  const raw = API_MOCK ? await mock.mockDecide(id, "reject", body) : (await request(`/api/proposals/${enc(id)}/reject`, { method: "POST", json: body, write: true })).body;
  return one(ProposalSchema, unwrap(raw, "proposal"), "a proposal");
}

export type ImportResult = { imported: number; skipped: number; errors: string[] };

const ImportResultSchema = z.object({
  imported: count,
  skipped: count,
  errors: textList,
});

/** `POST /api/apps/{id}/import` as multipart/form-data with the file in field `file`. */
export async function importReviews(appId: string, file: File): Promise<ImportResult> {
  if (API_MOCK) return ImportResultSchema.parse(await mock.mockImport(appId, file));
  const form = new FormData();
  form.append("file", file, file.name);
  const { body } = await request(`/api/apps/${enc(appId)}/import`, { method: "POST", body: form, write: true });
  const p = ImportResultSchema.safeParse(body);
  return p.success ? p.data : { imported: 0, skipped: 0, errors: [] };
}

/* ------------------------------------------------------------------ */
/* Processing (resumable)                                               */

export type ProcessOutcome = { outcome: "processed" | "claimed"; status?: RunStatus };

/**
 * Drive a run. The API runs on a serverless runtime with no background worker,
 * so after `POST /api/apps/{id}/runs` the client asks for processing:
 * `POST /api/runs/{id}/process` holds the request open (up to ~250 s). When the
 * API runs out of time it checkpoints and answers 202 `{ resumable: true }`; the
 * client asks again until the run finishes. 409 means another request already
 * claimed the run, which is fine: progress still arrives over SSE.
 */
export async function processRun(id: string): Promise<ProcessOutcome> {
  if (API_MOCK) return mock.mockProcess(id);
  const MAX_SEGMENTS = 12;
  for (let segment = 0; segment < MAX_SEGMENTS; segment++) {
    let res: Response;
    try {
      res = await fetch(`${API_URL}/api/runs/${enc(id)}/process`, {
        method: "POST",
        headers: { Accept: "application/json", ...authHeader() },
        keepalive: true,
        cache: "no-store",
      });
    } catch {
      throw networkError(API_URL);
    }
    const body = await readBody(res);
    if (res.status === 409) return { outcome: "claimed" };
    if (!res.ok) throw parseApiError(res.status, body, res.headers.get("Retry-After"));
    if (res.status === 202 && isResumable(body)) continue;
    const parsed = RunSchema.safeParse(unwrap(body, "run"));
    return { outcome: "processed", status: parsed.success ? parsed.data.status : undefined };
  }
  return { outcome: "claimed" };
}

export function isResumable(body: unknown): boolean {
  return typeof body === "object" && body !== null && (body as { resumable?: unknown }).resumable === true;
}

/** Processing requests started in this tab, by run id, so a remount does not re-POST. */
const inFlight = new Map<string, Promise<ProcessOutcome>>();

export function startProcessing(id: string, { force = false }: { force?: boolean } = {}): Promise<ProcessOutcome> {
  const existing = inFlight.get(id);
  if (existing && !force) return existing;
  const p = processRun(id);
  inFlight.set(id, p);
  p.catch(() => {
    if (inFlight.get(id) === p) inFlight.delete(id);
  });
  return p;
}

export function processingPromise(id: string): Promise<ProcessOutcome> | undefined {
  return inFlight.get(id);
}

export function isRetryableProcessError(err: unknown): boolean {
  return err instanceof ApiError && (err.code === "network" || err.status >= 500);
}

/* ------------------------------------------------------------------ */
/* Live events (SSE)                                                    */

export type RunEventHandlers = {
  onEvent: (event: RunEvent) => void;
  /** Connection-level failure (not an event of kind "failed"). */
  onError: (fatal: boolean) => void;
  onOpen?: () => void;
};

/** Subscribe to a run's events (replay, then tail). Returns an unsubscribe function. */
export function subscribeToRun(id: string, handlers: RunEventHandlers): () => void {
  if (API_MOCK) return mock.mockSubscribe(id, handlers);
  if (typeof EventSource === "undefined") {
    handlers.onError(true);
    return () => {};
  }
  const source = new EventSource(eventsUrl(id));
  let fallbackSeq = 0;
  const onMessage = (ev: MessageEvent) => {
    fallbackSeq += 1;
    const lastId = Number(ev.lastEventId);
    const parsed = parseRunEvent(String(ev.data ?? ""), Number.isFinite(lastId) && lastId > 0 ? lastId : fallbackSeq);
    if (parsed) handlers.onEvent(parsed);
  };
  source.onopen = () => handlers.onOpen?.();
  source.onmessage = onMessage;
  source.onerror = (ev) => {
    if (ev instanceof MessageEvent && ev.data) {
      onMessage(ev);
      return;
    }
    handlers.onError(source.readyState === EventSource.CLOSED);
  };
  return () => source.close();
}

/** Keep events unique by seq, in order. */
export function mergeEvent(prev: RunEvent[], e: RunEvent): RunEvent[] {
  if (prev.some((p) => p.seq === e.seq)) return prev;
  const next = [...prev, e];
  next.sort((a, b) => a.seq - b.seq);
  return next;
}

/* ------------------------------------------------------------------ */
/* Health                                                               */

export type Health = {
  web: { ok: true };
  api: { reachable: boolean; url: string; status?: number; body?: unknown; error?: string; mock?: boolean };
};
