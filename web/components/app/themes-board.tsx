"use client";

import { useState } from "react";
import { ChevronRightIcon } from "lucide-react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { ThemeKindChip, ThemeStatusChip } from "@/components/chips";
import { EmptyState, ErrorBox } from "@/components/error-box";
import { Sparkline } from "@/components/sparkline";
import { ReviewList } from "@/components/app/reviews-table";
import { useResource, type Resource } from "@/hooks/use-resource";
import { listReviews, type Theme } from "@/lib/api";
import { formatDate, plural } from "@/lib/format";

/**
 * Themes as a ranked board: rank, title, kind, a review-count bar drawn to scale
 * against the largest theme, and the sentiment sparkline on a fixed 1–5 scale.
 * Selecting a theme opens its member reviews.
 */
export function ThemesBoard({ appId, themes }: { appId: string; themes: Resource<Theme[]> }) {
  const [selected, setSelected] = useState<Theme | null>(null);

  if (themes.error) return <ErrorBox error={themes.error} />;
  if (!themes.data) return <Skeleton className="h-64 w-full" />;
  if (themes.data.length === 0) {
    return <EmptyState title="No themes yet">Themes appear after a run clusters at least a few related reviews.</EmptyState>;
  }

  const ranked = [...themes.data].sort((a, b) => b.review_count - a.review_count || a.title.localeCompare(b.title));
  const max = Math.max(1, ...ranked.map((t) => t.review_count));

  return (
    <>
      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <div
          aria-hidden="true"
          className="figure hidden grid-cols-[3rem_minmax(0,1fr)_10rem_9.5rem_2rem] gap-4 border-b border-border px-4 py-2 text-[0.6875rem] tracking-[0.08em] text-muted-foreground uppercase md:grid"
        >
          <span>rank</span>
          <span>theme</span>
          <span>reviews</span>
          <span>sentiment</span>
          <span />
        </div>
        <ol aria-label="Themes ranked by review count">
          {ranked.map((t, i) => (
            <li key={t.id} className="border-b border-border last:border-b-0">
              <button
                type="button"
                onClick={() => setSelected(t)}
                className="grid w-full grid-cols-[2.25rem_minmax(0,1fr)_1.25rem] items-center gap-x-3 gap-y-2 px-4 py-4 text-left hover:bg-muted/50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-foreground md:grid-cols-[3rem_minmax(0,1fr)_10rem_9.5rem_2rem] md:gap-x-4"
              >
                <span className="figure self-start text-2xl leading-none font-medium text-muted-foreground tabular-nums md:self-center">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span className="min-w-0 space-y-1.5">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-heading text-base leading-snug font-semibold sm:text-lg">{t.title}</span>
                  </span>
                  <span className="flex flex-wrap items-center gap-2">
                    <ThemeKindChip kind={t.kind} />
                    <ThemeStatusChip status={t.status} />
                    <span className="figure text-xs text-muted-foreground">{t.id}</span>
                  </span>
                  {t.summary ? <span className="line-clamp-2 block text-sm text-muted-foreground">{t.summary}</span> : null}
                </span>
                <ChevronRightIcon className="size-4 text-muted-foreground md:hidden" aria-hidden="true" />
                <span className="col-span-2 col-start-2 flex items-center gap-2 md:col-span-1 md:col-start-auto">
                  <span className="figure w-8 shrink-0 text-right text-sm tabular-nums">{t.review_count}</span>
                  <span aria-hidden="true" className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                    <span className="block h-full rounded-full bg-foreground/70" style={{ width: `${(t.review_count / max) * 100}%` }} />
                  </span>
                  <span className="sr-only">{plural(t.review_count, "review")}</span>
                </span>
                <span className="col-span-2 col-start-2 md:col-span-1 md:col-start-auto">
                  <Sparkline values={t.sentiment} />
                </span>
                <ChevronRightIcon className="hidden size-4 text-muted-foreground md:block" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ol>
      </div>
      <p className="figure mt-2 text-xs text-muted-foreground">
        Bars share one scale (the largest theme fills the bar). Sparklines plot the last ratings on a fixed 1–5 scale; the dashed rule is 3 stars.
      </p>

      <Sheet open={selected !== null} onOpenChange={(o) => !o && setSelected(null)}>
        <SheetContent side="right" className="w-full gap-0 overflow-y-auto sm:max-w-xl">
          {selected ? <ThemeDetail key={selected.id} appId={appId} theme={selected} /> : null}
        </SheetContent>
      </Sheet>
    </>
  );
}

function ThemeDetail({ appId, theme }: { appId: string; theme: Theme }) {
  const reviews = useResource(`reviews:${appId}:${theme.id}`, () => listReviews(appId, { theme: theme.id }));
  return (
    <>
      <SheetHeader className="border-b border-border p-5 pr-12">
        <p className="kicker">
          theme · {theme.kind} · {plural(theme.review_count, "review")}
        </p>
        <SheetTitle className="text-xl leading-snug font-semibold">{theme.title}</SheetTitle>
        <SheetDescription>{theme.summary ?? "No summary yet."}</SheetDescription>
        <div className="flex flex-wrap items-center gap-3 pt-2">
          <Sparkline values={theme.sentiment} width={140} height={32} />
          <span className="figure text-xs text-muted-foreground">updated {formatDate(theme.updated_at)}</span>
        </div>
      </SheetHeader>
      <div className="space-y-3 p-5">
        <h3 className="kicker">Member reviews</h3>
        {reviews.error ? <ErrorBox error={reviews.error} /> : null}
        {reviews.data ? (
          reviews.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">No member reviews returned.</p>
          ) : (
            <ReviewList reviews={reviews.data} />
          )
        ) : !reviews.error ? (
          <Skeleton className="h-40 w-full" />
        ) : null}
        {reviews.data && reviews.data.length < theme.review_count ? (
          <p className="figure text-xs text-muted-foreground">
            showing {reviews.data.length} of {theme.review_count}
          </p>
        ) : null}
      </div>
    </>
  );
}
