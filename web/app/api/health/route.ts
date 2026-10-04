import { API_MOCK, API_URL } from "@/lib/config";
import { mockHealth } from "@/lib/mock";
import type { Health } from "@/lib/api";

/**
 * Web health plus API reachability, so Vercel (or an uptime check) can see
 * whether the web app can talk to the API. 200 when the API answered OK, 503 otherwise.
 */
export const dynamic = "force-dynamic";

const TIMEOUT_MS = 5000;

export async function GET() {
  if (API_MOCK) {
    const body: Health = { web: { ok: true }, api: { reachable: true, url: "mock", status: 200, body: mockHealth(), mock: true } };
    return Response.json(body, { headers: { "Cache-Control": "no-store" } });
  }

  let body: Health;
  try {
    const res = await fetch(`${API_URL}/api/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
      headers: { Accept: "application/json" },
    });
    let json: unknown = null;
    try {
      json = await res.json();
    } catch {
      json = null;
    }
    body = { web: { ok: true }, api: { reachable: res.ok, url: API_URL, status: res.status, body: json } };
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    body = { web: { ok: true }, api: { reachable: false, url: API_URL, error: timedOut ? `no answer within ${TIMEOUT_MS} ms` : "unreachable" } };
  }
  return Response.json(body, { status: body.api.reachable ? 200 : 503, headers: { "Cache-Control": "no-store" } });
}
