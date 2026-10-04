/**
 * Runtime configuration. Both values are inlined at build time
 * (NEXT_PUBLIC_*), so the browser and the server agree on them.
 */

export const DEFAULT_API_URL = "http://localhost:7860";

/** Base URL of the FastAPI service, without a trailing slash. */
export const API_URL = normaliseBaseUrl(process.env.NEXT_PUBLIC_API_URL);

/** `NEXT_PUBLIC_API_MOCK=1` serves fixtures from lib/mock.ts instead of calling the API. */
export const API_MOCK = process.env.NEXT_PUBLIC_API_MOCK === "1";

export function normaliseBaseUrl(raw: string | undefined): string {
  const value = (raw ?? "").trim();
  return (value === "" ? DEFAULT_API_URL : value).replace(/\/+$/, "");
}
