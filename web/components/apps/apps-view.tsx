"use client";

import Link from "next/link";
import { ArrowRightIcon } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Chip, RunStatusChip } from "@/components/chips";
import { EmptyState, ErrorBox } from "@/components/error-box";
import { ReadOnlyNotice } from "@/components/operator/operator-provider";
import { useOperatorToken } from "@/hooks/use-operator-token";
import { useResource } from "@/hooks/use-resource";
import { listApps } from "@/lib/api";
import { formatCount, formatDateTime, storeLabel } from "@/lib/format";
import { AddAppDialog } from "./add-app-dialog";

export function AppsView() {
  const token = useOperatorToken();
  const apps = useResource("apps", listApps);

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-2">
          <p className="kicker">Apps on the scope</p>
          <h1 className="text-3xl font-bold sm:text-4xl">Apps</h1>
          <p className="max-w-xl text-muted-foreground">Each app has its own queue, themes and run history. Open one to review what the agent proposed.</p>
        </div>
        {token ? <AddAppDialog /> : <ReadOnlyNotice label="adding apps needs the token" />}
      </div>

      {apps.error ? <ErrorBox error={apps.error} /> : null}
      {!apps.data && !apps.error ? (
        <div className="grid gap-3" aria-busy="true">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : null}
      {apps.data && apps.data.length === 0 ? (
        <EmptyState title="No apps yet">{token ? "Add an App Store app to start." : "Add the operator token to add the first app."}</EmptyState>
      ) : null}
      {apps.data && apps.data.length > 0 ? (
        <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card" aria-label="Apps">
          {apps.data.map((a) => (
            <li key={a.id}>
              <Link
                href={`/apps/${encodeURIComponent(a.id)}`}
                className="group grid gap-4 px-5 py-5 hover:bg-muted/50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-foreground md:grid-cols-[minmax(0,1fr)_auto_auto] md:items-center md:gap-8"
              >
                <span className="min-w-0 space-y-1.5">
                  <span className="block font-heading text-lg font-semibold">{a.name}</span>
                  <span className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                    <Chip tone="outline">{storeLabel(a.store)}</Chip>
                    <span className="figure">{a.store_id}</span>
                    <span className="figure uppercase">{a.country}</span>
                    {a.github_repo ? <span className="figure truncate">→ {a.github_repo}</span> : null}
                  </span>
                </span>
                <span className="figure grid grid-cols-3 gap-6 text-sm">
                  <Count label="reviews" value={a.review_count} />
                  <Count label="themes" value={a.theme_count} />
                  <Count label="waiting" value={a.proposals_waiting} accent />
                </span>
                <span className="flex items-center gap-3 text-sm text-muted-foreground">
                  <span className="figure">
                    {a.last_run_at ? `last run ${formatDateTime(a.last_run_at)}` : "never run"}
                  </span>
                  {a.last_run ? <RunStatusChip status={a.last_run.status} /> : null}
                  <ArrowRightIcon className="size-4 shrink-0 motion-safe:transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Count({ label, value, accent = false }: { label: string; value: number; accent?: boolean }) {
  return (
    <span className="flex flex-col">
      <span className={accent && value > 0 ? "text-xl leading-none text-amber-ink" : "text-xl leading-none"}>{formatCount(value)}</span>
      <span className="mt-1 text-xs text-muted-foreground">{label}</span>
    </span>
  );
}
