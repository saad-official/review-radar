"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { ExternalLinkIcon, Loader2Icon, RadarIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Chip, RunStatusChip } from "@/components/chips";
import { ErrorBox } from "@/components/error-box";
import { ReadOnlyNotice, useOperator } from "@/components/operator/operator-provider";
import { useOperatorToken } from "@/hooks/use-operator-token";
import { useResource } from "@/hooks/use-resource";
import { createRun, getApp, listProposals, listReviews, listRuns, listThemes, startProcessing, type App } from "@/lib/api";
import { asApiError } from "@/lib/errors";
import { formatCount, formatDateTime, storeLabel } from "@/lib/format";
import { LiveRunStrip } from "./live-run-strip";
import { Queue } from "./queue";
import { ReviewsTable } from "./reviews-table";
import { RunsList } from "./runs-list";
import { SettingsPanel } from "./settings-panel";
import { ThemesBoard } from "./themes-board";

const TABS = ["queue", "themes", "reviews", "runs", "settings"] as const;
type Tab = (typeof TABS)[number];

export function AppView({ id }: { id: string }) {
  const token = useOperatorToken();
  const { reportError } = useOperator();
  const app = useResource(`app:${id}`, () => getApp(id));
  const proposals = useResource(`proposals:${id}`, () => listProposals(id, { status: "proposed" }));
  const themes = useResource(`themes:${id}`, () => listThemes(id));
  const [tab, setTab] = useState<Tab>("queue");
  // Lazy tabs: fetched the first time they are opened.
  const [seen, setSeen] = useState<Set<Tab>>(() => new Set<Tab>(["queue"]));
  const reviews = useResource(seen.has("reviews") ? `reviews:${id}` : null, () => listReviews(id));
  const runs = useResource(seen.has("runs") ? `runs:${id}` : null, () => listRuns(id));
  const [liveRunId, setLiveRunId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [liveRunning, setLiveRunning] = useState(false);

  const { reload: reloadApp } = app;
  const { reload: reloadProposals } = proposals;
  const { reload: reloadThemes } = themes;
  const { reload: reloadRuns } = runs;
  const { reload: reloadReviews } = reviews;

  const onTab = (value: string) => {
    const t = value as Tab;
    setTab(t);
    setSeen((prev) => (prev.has(t) ? prev : new Set(prev).add(t)));
  };

  const runNow = async () => {
    if (starting) return;
    setStarting(true);
    try {
      const run = await createRun(id);
      // The API has no background worker: this tab drives /process (resumable) while SSE reports progress.
      void startProcessing(run.id).catch(() => {});
      setLiveRunId(run.id);
      setLiveRunning(true);
      reloadRuns();
    } catch (err) {
      reportError(asApiError(err), "Run now");
    } finally {
      setStarting(false);
    }
  };

  const onRunFinished = useCallback(
    (status: "done" | "failed") => {
      setLiveRunning(false);
      if (status === "done") toast.success("Run finished", { description: "The queue, themes and counts are refreshed." });
      else toast.error("Run failed", { description: "Open the flight recorder to see which step failed." });
      reloadApp();
      reloadProposals();
      reloadThemes();
      reloadRuns();
      reloadReviews();
    },
    [reloadApp, reloadProposals, reloadThemes, reloadRuns, reloadReviews],
  );

  if (app.error) {
    return (
      <div className="space-y-6">
        <h1 className="text-3xl font-bold">App not available</h1>
        <ErrorBox error={app.error} />
        <Link href="/apps" className="text-sm underline underline-offset-4">
          Back to apps
        </Link>
      </div>
    );
  }

  const a = app.data;
  const themeTitles = new Map((themes.data ?? []).map((t) => [t.id, t.title]));
  const waiting = proposals.data ? proposals.data.filter((p) => p.status === "proposed").length : a?.proposals_waiting;

  return (
    <div className="space-y-8">
      <header className="space-y-6">
        <nav aria-label="Breadcrumb" className="figure text-xs text-muted-foreground">
          <ol className="flex flex-wrap items-center gap-1.5">
            <li>
              <Link href="/apps" className="hover:text-foreground">
                apps
              </Link>
            </li>
            <li aria-hidden="true">/</li>
            <li aria-current="page" className="text-foreground">
              {id}
            </li>
          </ol>
        </nav>

        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="min-w-0 space-y-2">
            {a ? (
              <h1 className="text-3xl leading-tight font-bold sm:text-4xl">{a.name}</h1>
            ) : (
              <>
                <h1 className="sr-only">App {id}</h1>
                <Skeleton className="h-10 w-72" />
              </>
            )}
            {a ? (
              <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <Chip tone="outline">{storeLabel(a.store)}</Chip>
                <span className="figure">id {a.store_id}</span>
                <span aria-hidden="true">·</span>
                <span className="figure uppercase">{a.country}</span>
                <span aria-hidden="true">·</span>
                {a.github_repo ? (
                  <a href={`https://github.com/${a.github_repo}`} className="figure inline-flex items-center gap-1 underline decoration-foreground/30 underline-offset-4 hover:decoration-foreground">
                    {a.github_repo}
                    <ExternalLinkIcon className="size-3" aria-hidden="true" />
                  </a>
                ) : (
                  <span>no GitHub repo connected</span>
                )}
              </p>
            ) : null}
          </div>
          <div className="flex flex-col items-start gap-2 sm:items-end">
            {token ? (
              <Button type="button" className="h-10 px-4" onClick={runNow} disabled={starting || liveRunning}>
                {starting ? <Loader2Icon className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : <RadarIcon aria-hidden="true" />}
                {liveRunning ? "Running…" : "Run now"}
              </Button>
            ) : (
              <>
                <Button type="button" className="h-10 px-4" disabled>
                  <RadarIcon aria-hidden="true" />
                  Run now
                </Button>
                <ReadOnlyNotice />
              </>
            )}
          </div>
        </div>

        {/* Overview as one instrument strip, not a grid of stat cards: the queue below is the point. */}
        <dl className="figure grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border text-sm md:grid-cols-[1fr_1fr_1fr_1.4fr]">
          <Tile label="new reviews" value={a ? (a.new_reviews !== undefined ? formatCount(a.new_reviews) : "—") : undefined} note={a ? `${formatCount(a.review_count)} total` : undefined} />
          <Tile label="themes open" value={a ? formatCount(themes.data ? themes.data.filter((t) => t.status === "open").length : a.theme_count) : undefined} />
          <Tile label="proposals waiting" value={waiting !== undefined ? formatCount(waiting) : undefined} accent={!!waiting} />
          <div className="col-span-2 min-w-0 bg-card px-4 py-3 md:col-span-1">
            <dt className="text-xs text-muted-foreground">last run</dt>
            <dd className="mt-1 flex flex-wrap items-center gap-2">
              {a ? (
                a.last_run ? (
                  <>
                    <Link href={`/runs/${encodeURIComponent(a.last_run.id)}`} className="underline decoration-foreground/30 underline-offset-4 hover:decoration-foreground">
                      {formatDateTime(a.last_run_at)}
                    </Link>
                    <RunStatusChip status={a.last_run.status} />
                  </>
                ) : (
                  <span>{a.last_run_at ? formatDateTime(a.last_run_at) : "never"}</span>
                )
              ) : (
                <Skeleton className="h-5 w-32" />
              )}
            </dd>
          </div>
        </dl>

        {liveRunId ? <LiveRunStrip key={liveRunId} runId={liveRunId} onFinished={onRunFinished} /> : null}
      </header>

      <Tabs value={tab} onValueChange={onTab} className="gap-6">
        <div className="-mx-4 overflow-x-auto overflow-y-hidden px-4 [scrollbar-width:none] sm:mx-0 sm:px-0">
          <TabsList variant="line" className="h-10 w-full justify-start gap-1 border-b border-border p-0">
            {TABS.map((t) => (
              <TabsTrigger key={t} value={t} className="h-10 flex-none px-3 text-sm capitalize group-data-horizontal/tabs:after:bottom-0">
                {t}
                {t === "queue" && waiting ? (
                  <span className="figure ml-1 rounded-sm bg-amber-wash px-1.5 text-xs text-amber-ink tabular-nums">{waiting}</span>
                ) : null}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>

        <TabsContent value="queue" className="space-y-4">
          <h2 className="sr-only">Approval queue</h2>
          <Queue proposals={proposals} themeTitles={themeTitles} onChanged={reloadApp} />
        </TabsContent>
        <TabsContent value="themes">
          <h2 className="sr-only">Themes</h2>
          <ThemesBoard appId={id} themes={themes} />
        </TabsContent>
        <TabsContent value="reviews">
          <h2 className="sr-only">Reviews</h2>
          <ReviewsTable reviews={reviews} />
        </TabsContent>
        <TabsContent value="runs">
          <h2 className="sr-only">Runs</h2>
          <RunsList runs={runs} />
        </TabsContent>
        <TabsContent value="settings">
          <h2 className="sr-only">Settings</h2>
          {a ? <SettingsPanel app={a} onSaved={(updated: App) => app.mutate(() => updated)} /> : <Skeleton className="h-64 w-full" />}
        </TabsContent>
      </Tabs>
    </div>
  );
}

function Tile({ label, value, note, accent = false }: { label: string; value: string | undefined; note?: string; accent?: boolean }) {
  return (
    <div className="min-w-0 bg-card px-4 py-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 flex items-baseline gap-2">
        {value === undefined ? (
          <Skeleton className="h-6 w-10" />
        ) : (
          <span className={accent ? "text-2xl leading-none font-medium text-amber-ink" : "text-2xl leading-none font-medium"}>{value}</span>
        )}
        {note ? <span className="text-xs text-muted-foreground">{note}</span> : null}
      </dd>
    </div>
  );
}
