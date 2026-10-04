import type { Evidence } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Quotes from source reviews, each with its review id and date: the agent's evidence. */
export function EvidenceList({ evidence, className }: { evidence: Evidence[]; className?: string }) {
  if (evidence.length === 0) return <p className="text-sm text-muted-foreground">No evidence attached.</p>;
  return (
    <ul className={cn("space-y-2", className)}>
      {evidence.map((e, i) => (
        <li key={`${e.review_id ?? "q"}-${i}`} className="border-l-2 border-amber/60 pl-3">
          <blockquote className="text-[0.9375rem] leading-snug">&ldquo;{e.quote}&rdquo;</blockquote>
          <p className="figure mt-0.5 text-xs text-muted-foreground">
            {e.review_id ?? "review"}
            {e.date ? ` · ${formatDate(e.date)}` : null}
          </p>
        </li>
      ))}
    </ul>
  );
}
