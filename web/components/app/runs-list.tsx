"use client";

import Link from "next/link";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { RunStatusChip } from "@/components/chips";
import { EmptyState, ErrorBox } from "@/components/error-box";
import type { Resource } from "@/hooks/use-resource";
import type { Run } from "@/lib/api";
import { durationBetween, formatCount, formatDateTime, formatDuration, formatUsd } from "@/lib/format";

export function RunsList({ runs }: { runs: Resource<Run[]> }) {
  if (runs.error) return <ErrorBox error={runs.error} />;
  if (!runs.data) return <Skeleton className="h-48 w-full" />;
  if (runs.data.length === 0) return <EmptyState title="No runs yet">Use Run now, or wait for the daily schedule.</EmptyState>;
  return (
    <div className="rounded-lg border border-border bg-card">
      <Table className="min-w-[44rem]" containerLabel="Runs table, scrollable">
        <TableCaption className="sr-only">Runs for this app, newest first.</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead className="pl-4">Run</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Started</TableHead>
            <TableHead className="text-right">Duration</TableHead>
            <TableHead className="text-right">Steps</TableHead>
            <TableHead className="text-right">Cost</TableHead>
            <TableHead className="pr-4">Summary</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.data.map((r) => (
            <TableRow key={r.id} className="align-top">
              <TableCell className="pl-4">
                <Link href={`/runs/${encodeURIComponent(r.id)}`} className="figure font-medium underline decoration-foreground/30 underline-offset-4 hover:decoration-foreground">
                  {r.id}
                </Link>
              </TableCell>
              <TableCell>
                <RunStatusChip status={r.status} />
              </TableCell>
              <TableCell className="figure text-sm whitespace-nowrap">{formatDateTime(r.started_at)}</TableCell>
              <TableCell className="figure text-right text-sm">{formatDuration(durationBetween(r.started_at, r.finished_at))}</TableCell>
              <TableCell className="figure text-right text-sm">{formatCount(r.step_count)}</TableCell>
              <TableCell className="figure text-right text-sm">{formatUsd(r.usage.usd)}</TableCell>
              <TableCell className="max-w-[22rem] pr-4 text-sm whitespace-normal text-muted-foreground">
                <span className="line-clamp-2">{r.error ?? r.summary ?? "—"}</span>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
