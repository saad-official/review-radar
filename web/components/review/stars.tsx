import { stars } from "@/lib/format";
import { cn } from "@/lib/utils";

export function Stars({ rating, className }: { rating: number; className?: string }) {
  const label = rating > 0 ? `${rating} of 5 stars` : "rating unknown";
  return (
    <span className={cn("figure text-[0.8125rem] tracking-tight whitespace-nowrap", rating > 0 && rating <= 2 ? "text-rose-ink" : "text-foreground/80", className)}>
      <span aria-hidden="true">{stars(rating)}</span>
      <span className="sr-only">{label}</span>
    </span>
  );
}
