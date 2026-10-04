"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { ArrowRightIcon } from "lucide-react";
import { RunStatusChip } from "@/components/chips";
import { ErrorBox } from "@/components/error-box";
import { isTerminal } from "@/lib/api";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useProcessing, useRunEvents } from "@/components/run/use-run-events";

const kindTone: Record<string, string> = {
  tool_call: "text-scope-blue",
  tool_result: "text-scope-green",
  model: "text-scope-amber",
  note: "text-scope-dim",
};

/** The run that "Run now" started, tailing its events until done|failed. */
export function LiveRunStrip({ runId, onFinished }: { runId: string; onFinished: (status: "done" | "failed") => void }) {
  const { events, status, connection } = useRunEvents(runId, true);
  const { processState, startProcessing } = useProcessing(runId);
  const reported = useRef(false);
  const onFinishedRef = useRef(onFinished);
  useEffect(() => {
    onFinishedRef.current = onFinished;
  });

  useEffect(() => {
    if (status && isTerminal(status) && !reported.current) {
      reported.current = true;
      onFinishedRef.current(status);
    }
  }, [status]);

  const tail = events.slice(-6);
  const running = !status || !isTerminal(status);

  return (
    <section aria-labelledby="live-run-title" className="overflow-hidden rounded-lg bg-scope text-scope-fg">
      <div className="flex flex-wrap items-center gap-3 border-b border-scope-rule px-4 py-2.5">
        <h2 id="live-run-title" className="figure text-sm font-medium tracking-normal">
          {running ? "Run in progress" : "Run finished"} · {runId}
        </h2>
        <RunStatusChip status={status ?? "queued"} />
        <span className="figure text-xs text-scope-dim">{connection}</span>
        <Link
          href={`/runs/${encodeURIComponent(runId)}`}
          className="figure ml-auto inline-flex items-center gap-1 text-xs text-scope-fg underline decoration-scope-dim underline-offset-4 hover:decoration-scope-fg focus-visible:outline-scope-fg"
        >
          Open flight recorder
          <ArrowRightIcon className="size-3" aria-hidden="true" />
        </Link>
      </div>
      <ol role="log" aria-live="polite" aria-relevant="additions" className="figure space-y-0.5 px-4 py-3 text-[0.8125rem]">
        {tail.map((e) => (
          <li key={e.seq} className="grid grid-cols-[auto_auto_1fr] gap-x-3">
            <span className="text-scope-dim tabular-nums">{formatClock(e.at)}</span>
            <span className={cn("w-[6ch]", kindTone[e.kind] ?? "text-scope-dim")}>{e.kind === "tool_result" ? "result" : e.kind === "tool_call" ? "call" : e.kind}</span>
            <span className="min-w-0 break-words text-scope-fg/90">{e.message}</span>
          </li>
        ))}
        {running ? (
          <li aria-hidden="true">
            <span className="caret text-scope-fg/70" />
          </li>
        ) : null}
      </ol>
      {processState.state === "error" ? (
        <div className="p-3 pt-0">
          <ErrorBox
            error={processState.error}
            action={
              <button type="button" onClick={() => startProcessing(true)} className="text-sm font-medium underline underline-offset-4">
                Resume processing
              </button>
            }
          />
        </div>
      ) : null}
    </section>
  );
}
