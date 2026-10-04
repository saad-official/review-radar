"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Chip, SeverityChip, ThemeKindChip } from "@/components/chips";
import { EmptyState, ErrorBox } from "@/components/error-box";
import { Stars } from "@/components/review/stars";
import type { Resource } from "@/hooks/use-resource";
import type { Review, Signals } from "@/lib/api";
import { formatDate } from "@/lib/format";

/** Model-extracted signals. Store fields (rating, version, date) are facts and shown apart from these. */
export function SignalChips({ signals }: { signals: Signals | null }) {
  if (!signals) return <span className="figure text-xs text-muted-foreground">not extracted</span>;
  const sentimentTone = signals.sentiment === "negative" ? "rose" : signals.sentiment === "positive" ? "radar" : "outline";
  return (
    <span className="flex flex-wrap gap-1.5">
      <ThemeKindChip kind={signals.category} />
      {signals.sentiment ? <Chip tone={sentimentTone}>{signals.sentiment}</Chip> : null}
      <SeverityChip severity={signals.severity} />
      {[...signals.devices, ...signals.os_versions.map((v) => `iOS ${v}`)].slice(0, 3).map((d) => (
        <Chip key={d} tone="outline">
          {d}
        </Chip>
      ))}
    </span>
  );
}

export function ReviewsTable({ reviews }: { reviews: Resource<Review[]> }) {
  if (reviews.error) return <ErrorBox error={reviews.error} />;
  if (!reviews.data) return <Skeleton className="h-72 w-full" />;
  if (reviews.data.length === 0) {
    return <EmptyState title="No reviews yet">Run the agent, or import a CSV of reviews from Settings.</EmptyState>;
  }
  return (
    <div className="rounded-lg border border-border bg-card">
      <Table className="min-w-[48rem]" containerLabel="Reviews table, scrollable">
        <TableCaption className="sr-only">Reviews, newest first. Signals are extracted by the model; rating, version and date come from the store.</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead className="w-28 pl-4">Rating</TableHead>
            <TableHead>Review</TableHead>
            <TableHead className="w-20">Version</TableHead>
            <TableHead className="w-28">Date</TableHead>
            <TableHead className="w-64 pr-4">Signals (model)</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {reviews.data.map((r) => (
            <TableRow key={r.id} className="align-top">
              <TableCell className="pl-4">
                <Stars rating={r.rating} />
              </TableCell>
              <TableCell className="max-w-[28rem] whitespace-normal">
                <p className="font-medium">{r.title ?? "Untitled"}</p>
                <p className="line-clamp-2 text-sm text-muted-foreground">{r.body}</p>
                <p className="figure mt-1 text-xs text-muted-foreground">
                  {r.id}
                  {r.author ? ` · ${r.author}` : null}
                </p>
              </TableCell>
              <TableCell className="figure text-sm">{r.app_version ?? "—"}</TableCell>
              <TableCell className="figure text-sm whitespace-nowrap">{formatDate(r.date)}</TableCell>
              <TableCell className="pr-4 whitespace-normal">
                <SignalChips signals={r.signals} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** Compact list for narrow places (the theme drawer). */
export function ReviewList({ reviews }: { reviews: Review[] }) {
  return (
    <ul className="space-y-3">
      {reviews.map((r) => (
        <li key={r.id} className="rounded-md border border-border p-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <Stars rating={r.rating} />
            <span className="figure text-xs text-muted-foreground">
              {r.id} · v{r.app_version ?? "?"} · {formatDate(r.date)}
            </span>
          </div>
          {r.title ? <p className="mt-1.5 font-medium">{r.title}</p> : null}
          <p className="mt-0.5 text-sm leading-relaxed text-foreground/85">{r.body}</p>
          <div className="mt-2">
            <SignalChips signals={r.signals} />
          </div>
        </li>
      ))}
    </ul>
  );
}
