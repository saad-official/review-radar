/**
 * Geometry for the sentiment sparkline. The y domain is fixed to the rating
 * scale (1–5) rather than fitted to the data, so two themes' lines can be
 * compared by eye: a flat line at the bottom always means 1-star reviews.
 */

export const RATING_MIN = 1;
export const RATING_MAX = 5;

export type SparkPoint = { x: number; y: number; value: number };

export function sparkPoints(values: number[], width: number, height: number, pad = 2): SparkPoint[] {
  const vs = values.filter((v) => Number.isFinite(v)).map((v) => Math.min(RATING_MAX, Math.max(RATING_MIN, v)));
  if (vs.length === 0) return [];
  const innerW = Math.max(0, width - pad * 2);
  const innerH = Math.max(0, height - pad * 2);
  const step = vs.length > 1 ? innerW / (vs.length - 1) : 0;
  return vs.map((value, i) => ({
    x: round(vs.length > 1 ? pad + i * step : width / 2),
    y: round(pad + ((RATING_MAX - value) / (RATING_MAX - RATING_MIN)) * innerH),
    value,
  }));
}

/** y of a rating on the same scale (for the midline at 3). */
export function ratingY(value: number, height: number, pad = 2): number {
  const innerH = Math.max(0, height - pad * 2);
  return round(pad + ((RATING_MAX - value) / (RATING_MAX - RATING_MIN)) * innerH);
}

export function pathFor(points: SparkPoint[]): string {
  return points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x} ${p.y}`).join(" ");
}

export function mean(values: number[]): number | undefined {
  const vs = values.filter((v) => Number.isFinite(v));
  if (vs.length === 0) return undefined;
  return vs.reduce((a, b) => a + b, 0) / vs.length;
}

export type SentimentTone = "low" | "mid" | "high";

/** Below 2.5 stars is "low" (rose), 2.5–3.5 "mid" (amber), above is "high" (green). */
export function sentimentTone(avg: number | undefined): SentimentTone {
  if (avg === undefined) return "mid";
  if (avg < 2.5) return "low";
  if (avg <= 3.5) return "mid";
  return "high";
}

/** A sentence for screen readers, in place of the drawing. */
export function describeSentiment(values: number[]): string {
  const avg = mean(values);
  if (avg === undefined) return "No ratings yet.";
  const first = values[0];
  const last = values[values.length - 1];
  return `Last ${values.length} ratings average ${avg.toFixed(1)} of 5, from ${first} to ${last} stars.`;
}

function round(n: number): number {
  return Math.round(n * 100) / 100;
}
