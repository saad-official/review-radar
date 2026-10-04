/** Formatting shared by pages, the trajectory viewer and tests. All pure. */

const usdFull = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Dollars at the precision LLM runs need: $0.0061, $0.042, $1.20. */
export function formatUsd(usd: number | undefined): string {
  if (usd === undefined || !Number.isFinite(usd) || usd <= 0) return "$0.00";
  if (usd < 0.0001) return "<$0.0001";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  if (usd < 1) return `$${usd.toFixed(3).replace(/(\.\d\d)0$/, "$1")}`;
  return usdFull.format(usd);
}

const int = new Intl.NumberFormat("en-US");
export function formatCount(n: number | undefined): string {
  return n !== undefined && Number.isFinite(n) ? int.format(Math.round(n)) : "0";
}

/** 1,234 -> "1.2k" for tight table cells. */
export function formatCompact(n: number | undefined): string {
  if (n === undefined || !Number.isFinite(n)) return "0";
  if (Math.abs(n) < 1000) return String(Math.round(n));
  if (Math.abs(n) < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parse(iso: string | undefined): Date | undefined {
  if (!iso) return undefined;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? undefined : d;
}

/** "3 Oct 2026" (UTC, so server and browser agree). */
export function formatDate(iso: string | undefined): string {
  const d = parse(iso);
  if (!d) return "—";
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** "3 Oct, 06:02 UTC". */
export function formatDateTime(iso: string | undefined): string {
  const d = parse(iso);
  if (!d) return "—";
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}, ${hh}:${mm} UTC`;
}

/** "06:00:12" (UTC), the clock column of the flight recorder. */
export function formatClock(iso: string | undefined): string {
  const d = parse(iso);
  if (!d) return "--:--:--";
  return [d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
}

/** "+12.4 s" since the first step. */
export function formatOffset(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "+0.0 s";
  if (ms < 60_000) return `+${(ms / 1000).toFixed(1)} s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `+${m}m ${String(s).padStart(2, "0")}s`;
}

export function formatDuration(ms: number | undefined): string {
  if (ms === undefined || !Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m} min ${s} s`;
}

export function durationBetween(start: string | undefined, end: string | undefined): number | undefined {
  const a = parse(start);
  const b = parse(end);
  if (!a || !b) return undefined;
  return b.getTime() - a.getTime();
}

/** "2 h ago", for client components only (depends on the clock). */
export function formatRelative(iso: string | undefined, now: number = Date.now()): string {
  const d = parse(iso);
  if (!d) return "never";
  const s = Math.round((now - d.getTime()) / 1000);
  if (s < 0) return formatDateTime(iso);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86_400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86_400 * 14) return `${Math.floor(s / 86_400)} d ago`;
  return formatDate(iso);
}

/** Share of a budget used, clamped to [0, 1]. */
export function budgetShare(usd: number, max: number | undefined): number | undefined {
  if (!max || max <= 0 || !Number.isFinite(usd)) return undefined;
  return Math.min(1, Math.max(0, usd / max));
}

/** "★★★☆☆" for a 1–5 rating; 0 means unknown. */
export function stars(rating: number): string {
  const r = Math.min(5, Math.max(0, Math.round(rating)));
  return r === 0 ? "—" : "★".repeat(r) + "☆".repeat(5 - r);
}

export function storeLabel(store: string): string {
  return store === "android" ? "Google Play" : "App Store";
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${formatCount(n)} ${n === 1 ? one : many}`;
}

/** Accept a bare App Store id or an App Store URL (…/id6450012345). */
export function parseAppStoreId(input: string): string | undefined {
  const v = input.trim();
  if (/^\d{6,12}$/.test(v)) return v;
  const m = v.match(/\/id(\d{6,12})(?:[/?#]|$)/);
  return m ? m[1] : undefined;
}
