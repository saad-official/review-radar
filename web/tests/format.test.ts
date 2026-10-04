import { describe, expect, it } from "vitest";
import {
  budgetShare,
  durationBetween,
  formatClock,
  formatCompact,
  formatDate,
  formatDateTime,
  formatOffset,
  formatRelative,
  formatUsd,
  parseAppStoreId,
  plural,
  stars,
} from "@/lib/format";

describe("format", () => {
  it("formats dollars at LLM precision", () => {
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(0.00004)).toBe("<$0.0001");
    expect(formatUsd(0.0061)).toBe("$0.0061");
    expect(formatUsd(0.042)).toBe("$0.042");
    expect(formatUsd(1.2)).toBe("$1.20");
    expect(formatUsd(0.1)).toBe("$0.10");
    expect(formatUsd(undefined)).toBe("$0.00");
  });

  it("formats dates in UTC", () => {
    expect(formatDate("2026-10-03T23:30:00Z")).toBe("3 Oct 2026");
    expect(formatDateTime("2026-10-04T06:00:03Z")).toBe("4 Oct, 06:00 UTC");
    expect(formatClock("2026-10-04T06:00:03Z")).toBe("06:00:03");
    expect(formatDate(undefined)).toBe("—");
    expect(formatDate("not a date")).toBe("—");
  });

  it("formats offsets, durations and relative times", () => {
    expect(formatOffset(12_400)).toBe("+12.4 s");
    expect(formatOffset(75_000)).toBe("+1m 15s");
    expect(durationBetween("2026-10-04T06:00:00Z", "2026-10-04T06:00:30Z")).toBe(30_000);
    const now = Date.parse("2026-10-04T09:00:00Z");
    expect(formatRelative("2026-10-04T06:00:00Z", now)).toBe("3 h ago");
    expect(formatRelative(undefined, now)).toBe("never");
  });

  it("counts, shares, stars and plurals", () => {
    expect(formatCompact(18_420)).toBe("18k");
    expect(formatCompact(1_234)).toBe("1.2k");
    expect(budgetShare(0.05, 0.1)).toBe(0.5);
    expect(budgetShare(0.5, 0.1)).toBe(1);
    expect(budgetShare(0.05, undefined)).toBeUndefined();
    expect(stars(3)).toBe("★★★☆☆");
    expect(stars(0)).toBe("—");
    expect(plural(1, "review")).toBe("1 review");
    expect(plural(14, "review")).toBe("14 reviews");
  });

  it("reads App Store ids from ids and links", () => {
    expect(parseAppStoreId(" 6450012345 ")).toBe("6450012345");
    expect(parseAppStoreId("https://apps.apple.com/us/app/fieldnote/id6450012345?l=en")).toBe("6450012345");
    expect(parseAppStoreId("com.example.app")).toBeUndefined();
    expect(parseAppStoreId("12")).toBeUndefined();
  });
});
