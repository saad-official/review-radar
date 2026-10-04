import { describe, expect, it } from "vitest";
import { ApiError, asApiError, codeFromStatus, errorTitle, parseApiError } from "@/lib/errors";

describe("parseApiError", () => {
  it("reads the contract shape", () => {
    const e = parseApiError(429, { detail: { code: "rate_limited", message: "slow down", retry_after: 30 } });
    expect(e).toMatchObject({ status: 429, code: "rate_limited", message: "slow down", retryAfter: 30 });
    expect(errorTitle(e)).toBe("Slow down");
  });

  it("maps statuses when no code is sent", () => {
    expect(parseApiError(401, { detail: "Not authenticated" }).code).toBe("unauthorized");
    expect(parseApiError(404, {}).code).toBe("not_found");
    expect(parseApiError(409, {}).code).toBe("conflict");
    expect(parseApiError(503, "<html>").code).toBe("server");
    expect(codeFromStatus(418)).toBe("unknown");
  });

  it("reads FastAPI validation errors into fields", () => {
    const e = parseApiError(422, { detail: [{ loc: ["body", "store_id"], msg: "Field required" }, { loc: ["body"], msg: "bad" }] });
    expect(e.code).toBe("invalid_input");
    expect(e.fields).toEqual({ store_id: "Field required" });
    expect(e.message).toBe("store_id: Field required; bad");
  });

  it("accepts aliases, flat and nested error bodies, and Retry-After", () => {
    expect(parseApiError(400, { detail: { code: "validation", message: "x" } }).code).toBe("invalid_input");
    expect(parseApiError(400, { error: { code: "not_found", message: "gone" } })).toMatchObject({ code: "not_found", message: "gone" });
    expect(parseApiError(429, {}, "12").retryAfter).toBe(12);
    expect(parseApiError(500, "plain text failure").message).toBe("plain text failure");
  });

  it("wraps unknown throwables", () => {
    expect(asApiError(new Error("boom"))).toBeInstanceOf(ApiError);
    expect(asApiError("x").code).toBe("unknown");
  });
});
