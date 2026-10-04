import { afterEach, describe, expect, it, vi } from "vitest";
import { ProposalSchema } from "@/lib/api";
import { DEMO_APP_ID, DEMO_RUN_ID, mockDecide, mockGetRun, mockGetSteps, mockListProposals } from "@/lib/mock";
import { TOKEN_KEY } from "@/lib/operator";

function withToken(token: string | null) {
  const store = new Map<string, string>(token ? [[TOKEN_KEY, token]] : []);
  vi.stubGlobal("window", {
    localStorage: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => store.set(k, v),
      removeItem: (k: string) => store.delete(k),
    },
    addEventListener: () => {},
    removeEventListener: () => {},
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("mock API", () => {
  it("serves the finished demo run and its whole trajectory", async () => {
    const run = (await mockGetRun(DEMO_RUN_ID)) as { status: string; step_count: number };
    expect(run.status).toBe("done");
    const steps = (await mockGetSteps(DEMO_RUN_ID)) as unknown[];
    expect(steps).toHaveLength(run.step_count);
  });

  it("refuses writes without the operator token", async () => {
    withToken(null);
    await expect(mockDecide("prop_102", "approve", {})).rejects.toMatchObject({ status: 401, code: "unauthorized" });
  });

  it("approves an issue into an executed proposal with a URL, once", async () => {
    withToken("any");
    const p = ProposalSchema.parse(await mockDecide("prop_101", "approve", { draft: { title: "Edited title" } }));
    expect(p.status).toBe("executed");
    expect(p.draft.title).toBe("Edited title");
    expect(p.result.url).toMatch(/\/issues\/\d+$/);
    await expect(mockDecide("prop_101", "approve", {})).rejects.toMatchObject({ code: "conflict" });
    const waiting = (await mockListProposals(DEMO_APP_ID, { status: "proposed" })) as unknown[];
    expect(waiting).toHaveLength(4);
  });

  it("stores the rejection reason", async () => {
    withToken("any");
    const p = ProposalSchema.parse(await mockDecide("prop_103", "reject", { reason: "duplicate of #38" }));
    expect(p).toMatchObject({ status: "rejected", reason: "duplicate of #38" });
  });
});
