import { describe, expect, it } from "vitest";
import { checkCsvHeader, splitCsvLine } from "@/lib/csv";
import { describeSentiment, mean, pathFor, ratingY, sentimentTone, sparkPoints } from "@/lib/sparkline";

describe("sparkline", () => {
  it("draws on a fixed 1–5 scale, not fitted to the data", () => {
    const pts = sparkPoints([1, 1, 1], 100, 24, 2);
    expect(pts.map((p) => p.y)).toEqual([22, 22, 22]);
    expect(pts.map((p) => p.x)).toEqual([2, 50, 98]);
    expect(sparkPoints([5], 100, 24, 2)).toEqual([{ x: 50, y: 2, value: 5 }]);
    expect(ratingY(3, 24, 2)).toBe(12);
    expect(sparkPoints([], 100, 24)).toEqual([]);
    expect(sparkPoints([9, 0], 10, 10, 0).map((p) => p.value)).toEqual([5, 1]);
  });

  it("builds a path, a mean, a tone and a description", () => {
    expect(
      pathFor([
        { x: 0, y: 1, value: 1 },
        { x: 2, y: 3, value: 1 },
      ]),
    ).toBe("M0 1 L2 3");
    expect(mean([1, 2, 3])).toBe(2);
    expect(mean([])).toBeUndefined();
    expect(sentimentTone(1.4)).toBe("low");
    expect(sentimentTone(3)).toBe("mid");
    expect(sentimentTone(4.1)).toBe("high");
    expect(describeSentiment([2, 1, 1])).toBe("Last 3 ratings average 1.3 of 5, from 2 to 1 stars.");
  });
});

describe("csv header", () => {
  it("splits quoted cells", () => {
    expect(splitCsvLine('a,"b, c","d ""e"""')).toEqual(["a", "b, c", 'd "e"']);
  });

  it("accepts the required columns in any order, with a BOM", () => {
    expect(checkCsvHeader("﻿Rating,store_review_id,date,body,author\n1,2,3,4,5").ok).toBe(true);
  });

  it("names the missing columns", () => {
    const r = checkCsvHeader("id,rating,text\n");
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("store_review_id, body, date");
    expect(checkCsvHeader("").ok).toBe(false);
  });
});
