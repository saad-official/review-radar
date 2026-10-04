import { cn } from "@/lib/utils";
import { describeSentiment, mean, pathFor, ratingY, sentimentTone, sparkPoints } from "@/lib/sparkline";

const toneFill = { low: "fill-rose", mid: "fill-amber", high: "fill-radar" } as const;

/**
 * Sentiment sparkline: the last N star ratings, drawn on a fixed 1–5 scale so
 * lines are comparable across themes. A dashed rule marks 3 stars. The line is
 * charcoal; only the end dot carries the tone (low / mid / high), and the
 * average is printed beside it, so colour is never the only signal.
 */
export function Sparkline({
  values,
  width = 96,
  height = 28,
  className,
  showMean = true,
}: {
  values: number[];
  width?: number;
  height?: number;
  className?: string;
  showMean?: boolean;
}) {
  const pts = sparkPoints(values, width, height, 4);
  const avg = mean(values);
  const last = pts[pts.length - 1];
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <svg
        role="img"
        aria-label={describeSentiment(values)}
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        className="shrink-0 overflow-visible text-foreground"
      >
        <line x1={0} x2={width} y1={ratingY(3, height, 4)} y2={ratingY(3, height, 4)} className="stroke-border" strokeWidth={1} strokeDasharray="2 3" />
        {pts.length > 1 ? (
          <path d={pathFor(pts)} fill="none" stroke="currentColor" strokeOpacity={0.75} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
        ) : null}
        {pts.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={6} fill="transparent">
            <title>{`${p.value} star${p.value === 1 ? "" : "s"}`}</title>
          </circle>
        ))}
        {last ? <circle cx={last.x} cy={last.y} r={3} className={cn(toneFill[sentimentTone(avg)], "stroke-card")} strokeWidth={1.5} /> : null}
      </svg>
      {showMean && avg !== undefined ? (
        <span className="figure text-xs text-muted-foreground tabular-nums" aria-hidden="true">
          {avg.toFixed(1)}★
        </span>
      ) : null}
    </span>
  );
}
