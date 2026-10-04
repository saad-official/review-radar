"use client";

import { useEffect } from "react";
import Link from "next/link";
import { Loader2Icon, PlayIcon, RotateCwIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { RunStatusChip } from "@/components/chips";
import { EmptyState, ErrorBox } from "@/components/error-box";
import { ProposalCard } from "@/components/app/proposal-card";
import { ReadOnlyNotice } from "@/components/operator/operator-provider";
import { useOperatorToken } from "@/hooks/use-operator-token";
import { useResource } from "@/hooks/use-resource";
import { getApp, getRun, getRunSteps, isTerminal, listProposals, listThemes, type Proposal } from "@/lib/api";
import { budgetShare, durationBetween, formatCount, formatDateTime, formatDuration, formatUsd } from "@/lib/format";
import { ApiError } from "@/lib/errors";
import { cn } from "@/lib/utils";
import { TrajectoryTable } from "./trajectory-table";
import { useProcessing, useRunEvents, type Connection } from "./use-run-events";

const connectionLabel: Record<Connection, string> = {
  idle: "recorded",
  connecting: "connecting",
  live: "live",
  polling: "polling",
  closed: "complete",
};

export function RunView({ id }: { id: string }) {
  const token = useOperatorToken();
  const run = useResource(`run:${id}`, () => getRun(id));
  const steps = useResource(`steps:${id}`, () => getRunSteps(id));
  const appId = run.data?.app_id ?? null;
  const app = useResource(appId ? `app:${appId}` : null, () => getApp(appId!));
  const proposals = useResource(appId ? `proposals:${appId}:${id}` : null, () => listProposals(appId!, { run: id }));
  const themes = useResource(appId ? `themes:${appId}` : null, () => listThemes(appId!));

  const knownTerminal = run.data ? isTerminal(run.data.status) : false;
  const live = useRunEvents(id, run.data !== undefined && !knownTerminal);
  const { processState, startProcessing } = useProcessing(id);

  const status = live.status && run.data && !isTerminal(run.data.status) ? live.status : run.data?.status;
  const running = status !== undefined && !isTerminal(status);

  // Each live event means new steps on the server: re-read them (debounced).
  const { reload: reloadSteps } = steps;
  const { reload: reloadRun } = run;
  const { reload: reloadProposals } = proposals;
  useEffect(() => {
    if (live.events.length === 0) return;
    const t = setTimeout(() => {
      reloadSteps();
      reloadRun();
    }, 350);
    return () => clearTimeout(t);
  }, [live.events.length, reloadSteps, reloadRun]);

  useEffect(() => {
    if (!live.status || !isTerminal(live.status)) return;
    const t = setTimeout(() => {
      reloadRun();
      reloadSteps();
      reloadProposals();
    }, 500);
    return () => clearTimeout(t);
  }, [live.status, reloadRun, reloadSteps, reloadProposals]);

  if (run.error) {
    return (
      <div className="space-y-6">
        <h1 className="figure text-3xl font-medium">Run {id}</h1>
        <ErrorBox error={run.error} />
        <Link href="/apps" className="text-sm underline underline-offset-4">
          Back to apps
        </Link>
      </div>
    );
  }

  const r = run.data;
  const share = r ? budgetShare(r.usage.usd, r.usage.max_usd) : undefined;
  const themeTitles = new Map((themes.data ?? []).map((t) => [t.id, t.title]));
  const onDecided = (p: Proposal) => proposals.mutate((prev) => prev?.map((x) => (x.id === p.id ? p : x)));

  return (
    <div className="space-y-10">
      <header className="space-y-5">
        <nav aria-label="Breadcrumb" className="figure text-xs text-muted-foreground">
          <ol className="flex flex-wrap items-center gap-1.5">
            <li>
              <Link href="/apps" className="hover:text-foreground">
                apps
              </Link>
            </li>
            <li aria-hidden="true">/</li>
            <li>
              {appId ? (
                <Link href={`/apps/${encodeURIComponent(appId)}`} className="hover:text-foreground">
                  {app.data?.name ?? appId}
                </Link>
              ) : (
                "…"
              )}
            </li>
            <li aria-hidden="true">/</li>
            <li aria-current="page" className="text-foreground">
              {id}
            </li>
          </ol>
        </nav>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <h1 className="text-3xl font-bold sm:text-4xl">
            Run <span className="figure font-medium">{id}</span>
          </h1>
          {status ? <RunStatusChip status={status} /> : <Skeleton className="h-6 w-16" />}
          <span className="figure inline-flex items-center gap-1.5 text-xs text-muted-foreground" aria-live="polite">
            <span
              aria-hidden="true"
              className={cn(
                "size-1.5 rounded-full",
                live.connection === "live" ? "bg-radar motion-safe:animate-pulse" : live.connection === "polling" || live.connection === "connecting" ? "bg-amber" : "bg-slate/60",
              )}
            />
            {connectionLabel[live.connection]}
          </span>
        </div>

        {r ? (
          <dl className="figure grid grid-cols-2 gap-x-6 gap-y-3 rounded-lg border border-border bg-card px-5 py-4 text-sm sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="started" value={formatDateTime(r.started_at)} />
            <Stat label="duration" value={r.finished_at ? formatDuration(durationBetween(r.started_at, r.finished_at)) : running ? "running" : "—"} />
            <Stat label="steps" value={formatCount(Math.max(r.step_count, steps.data?.length ?? 0))} />
            <Stat label="tokens in / out" value={`${formatCount(r.usage.prompt_tokens)} / ${formatCount(r.usage.completion_tokens)}`} />
            <Stat label="cost" value={formatUsd(r.usage.usd)} />
            <div className="min-w-0">
              <dt className="text-xs text-muted-foreground">budget used</dt>
              <dd className="mt-1">
                {share !== undefined ? (
                  <>
                    <span>
                      {Math.round(share * 100)}% of {formatUsd(r.usage.max_usd)}
                    </span>
                    <span aria-hidden="true" className="mt-1.5 block h-1.5 w-full overflow-hidden rounded-full bg-muted">
                      <span className={cn("block h-full rounded-full", share > 0.9 ? "bg-rose" : share > 0.6 ? "bg-amber" : "bg-radar")} style={{ width: `${Math.max(2, share * 100)}%` }} />
                    </span>
                  </>
                ) : (
                  "—"
                )}
              </dd>
            </div>
          </dl>
        ) : (
          <Skeleton className="h-20 w-full" />
        )}

        <ProcessControls
          running={running}
          hasToken={!!token}
          processState={processState}
          onStart={() => startProcessing(processState.state === "error")}
        />
      </header>

      {r?.error ? <ErrorBox title="Run failed" error={new ApiError({ status: 0, code: "server", message: r.error })} /> : null}

      {r?.summary ? (
        <section aria-labelledby="summary-title" className="max-w-3xl space-y-2">
          <h2 id="summary-title" className="kicker">
            Run summary
          </h2>
          <p className="text-lg leading-relaxed">{r.summary}</p>
        </section>
      ) : null}

      <section aria-labelledby="trajectory-title" className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 id="trajectory-title" className="text-2xl font-semibold">
              Trajectory
            </h2>
            <p className="text-sm text-muted-foreground">
              Every tool call and result, in order, as the agent recorded them. Expand a row for the full arguments and result.
            </p>
          </div>
          {running ? <span className="figure text-xs text-amber-ink">recording…</span> : null}
        </div>
        {steps.error ? <ErrorBox error={steps.error} /> : null}
        {steps.data ? (
          <TrajectoryTable steps={steps.data} live={running} caption={`Trajectory of run ${id}`} />
        ) : !steps.error ? (
          <Skeleton className="h-72 w-full" />
        ) : null}
      </section>

      <section aria-labelledby="proposals-title" className="space-y-4">
        <h2 id="proposals-title" className="text-2xl font-semibold">
          Proposals from this run
        </h2>
        {proposals.error ? <ErrorBox error={proposals.error} /> : null}
        {proposals.data ? (
          proposals.data.length === 0 ? (
            <EmptyState title={running ? "Nothing proposed yet" : "Nothing proposed"}>
              {running ? "Proposals appear here as the run drafts them." : "This run linked its reviews to existing themes, or found nothing that needed a reply or an issue."}
            </EmptyState>
          ) : (
            <div className="grid gap-5">
              {proposals.data.map((p) => (
                <ProposalCard key={p.id} proposal={p} themeTitle={p.theme_id ? themeTitles.get(p.theme_id) : undefined} onDecided={onDecided} />
              ))}
            </div>
          )
        ) : !proposals.error ? (
          <Skeleton className="h-40 w-full" />
        ) : null}
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 truncate">{value}</dd>
    </div>
  );
}

function ProcessControls({
  running,
  hasToken,
  processState,
  onStart,
}: {
  running: boolean;
  hasToken: boolean;
  processState: ReturnType<typeof useProcessing>["processState"];
  onStart: () => void;
}) {
  if (processState.state === "running") {
    return (
      <p className="figure inline-flex items-center gap-2 text-xs text-muted-foreground" role="status">
        <Loader2Icon className="size-3.5 motion-safe:animate-spin" aria-hidden="true" />
        processing from this tab (resumable: the API checkpoints and this tab asks again)
      </p>
    );
  }
  if (processState.state === "error") {
    return (
      <ErrorBox
        error={processState.error}
        action={
          hasToken ? (
            <Button type="button" variant="outline" size="sm" onClick={onStart}>
              <RotateCwIcon aria-hidden="true" />
              Resume processing
            </Button>
          ) : (
            <ReadOnlyNotice />
          )
        }
      />
    );
  }
  if (!running || processState.state === "ok") return null;
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
      <span>Runs only advance while a request is processing them. If nobody is, resume it here.</span>
      {hasToken ? (
        <Button type="button" variant="outline" size="sm" onClick={onStart}>
          <PlayIcon aria-hidden="true" />
          Process this run
        </Button>
      ) : (
        <ReadOnlyNotice />
      )}
    </div>
  );
}
