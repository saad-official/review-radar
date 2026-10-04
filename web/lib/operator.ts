/**
 * The operator token: a single shared secret (`OPERATOR_TOKEN` on the API) that
 * unlocks write routes. There are no accounts. The token lives in this browser's
 * localStorage only, and every access is wrapped in try/catch because storage can
 * be blocked (private windows, cleared site data, embedded previews).
 *
 * Exposed as a tiny external store so React can read it with useSyncExternalStore
 * (no effects, no hydration mismatch: the server snapshot is always null).
 */

export const TOKEN_KEY = "review-radar.operator-token";

const listeners = new Set<() => void>();
/** In-memory fallback when localStorage throws, so the token still works for this tab. */
let memoryToken: string | null = null;

export function readOperatorToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.localStorage.getItem(TOKEN_KEY);
    return v && v.trim() !== "" ? v : memoryToken;
  } catch {
    return memoryToken;
  }
}

export function writeOperatorToken(token: string | null): void {
  const value = token?.trim() ? token.trim() : null;
  memoryToken = value;
  try {
    if (value) window.localStorage.setItem(TOKEN_KEY, value);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // storage blocked: the in-memory copy above still serves this tab
  }
  for (const l of listeners) l();
}

export function subscribeOperatorToken(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key === null || e.key === TOKEN_KEY) listener();
  };
  if (typeof window !== "undefined") window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    if (typeof window !== "undefined") window.removeEventListener("storage", onStorage);
  };
}

/** `abcd…wxyz` for display; never show the whole secret. */
export function maskToken(token: string): string {
  const t = token.trim();
  if (t.length <= 8) return "•".repeat(Math.max(4, t.length));
  return `${t.slice(0, 4)}…${t.slice(-4)}`;
}
