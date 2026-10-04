"use client";

import { Fragment, useState } from "react";
import { ChevronRightIcon } from "lucide-react";
import type { RunStep } from "@/lib/api";
import { formatClock, formatCount, formatOffset } from "@/lib/format";
import { cumulativeTokens, offsetsFromStart, prettyJson, stepError, stepHeadline, stepTokens, stepTone, type StepTone } from "@/lib/trajectory";
import { cn } from "@/lib/utils";

const kindLabel: Record<RunStep["kind"], string> = {
  tool_call: "call",
  tool_result: "result",
  model: "model",
  note: "note",
};

const toneText: Record<StepTone, string> = {
  call: "text-scope-blue",
  result: "text-scope-green",
  model: "text-scope-amber",
  note: "text-scope-dim",
  error: "text-scope-rose",
};

/**
 * The trajectory as a flight recorder: one row per step with sequence, clock,
 * kind, tool, a one-line summary of args (calls) or result (results), tokens and
 * the running total. Every row expands to the full JSON.
 */
export function TrajectoryTable({
  steps,
  live = false,
  caption,
  expandable = true,
  className,
}: {
  steps: RunStep[];
  live?: boolean;
  caption?: string;
  expandable?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState<Set<number>>(() => new Set());
  const cum = cumulativeTokens(steps);
  const offsets = offsetsFromStart(steps);

  const toggle = (seq: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(seq)) next.delete(seq);
      else next.add(seq);
      return next;
    });

  return (
    <div className={cn("min-w-0 overflow-hidden rounded-lg bg-scope text-scope-fg", className)}>
      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={caption ?? "Run trajectory, scrollable"}>
        <table className="figure w-full min-w-[46rem] border-collapse text-left text-[0.8125rem]">
          {caption ? <caption className="sr-only">{caption}</caption> : null}
          <thead>
            <tr className="border-b border-scope-rule text-[0.6875rem] tracking-[0.08em] text-scope-dim uppercase">
              <th scope="col" className="w-16 py-2 pr-2 pl-4 font-medium">
                seq
              </th>
              <th scope="col" className="w-28 px-2 py-2 font-medium">
                time
              </th>
              <th scope="col" className="w-16 px-2 py-2 font-medium">
                kind
              </th>
              <th scope="col" className="w-44 px-2 py-2 font-medium">
                tool
              </th>
              <th scope="col" className="px-2 py-2 font-medium">
                args · result
              </th>
              <th scope="col" className="w-20 px-2 py-2 text-right font-medium">
                tokens
              </th>
              <th scope="col" className="w-20 py-2 pr-4 pl-2 text-right font-medium">
                cum.
              </th>
            </tr>
          </thead>
          <tbody>
            {steps.map((s, i) => {
              const tone = stepTone(s);
              const tokens = stepTokens(s);
              const isOpen = open.has(s.seq);
              const err = stepError(s);
              const detailId = `step-${s.seq}-detail`;
              return (
                <Fragment key={s.seq}>
                  <tr className={cn("border-b border-scope-rule/60 align-top", isOpen && "bg-scope-raised")}>
                    <td className="py-1.5 pr-2 pl-4 text-scope-dim tabular-nums">
                      {expandable ? (
                        <button
                          type="button"
                          onClick={() => toggle(s.seq)}
                          aria-expanded={isOpen}
                          aria-controls={detailId}
                          className="inline-flex items-center gap-1 rounded-sm hover:text-scope-fg focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-scope-fg"
                        >
                          <ChevronRightIcon className={cn("size-3 motion-safe:transition-transform", isOpen && "rotate-90")} aria-hidden="true" />
                          <span className="sr-only">Step details for</span>
                          {String(s.seq).padStart(3, "0")}
                        </button>
                      ) : (
                        String(s.seq).padStart(3, "0")
                      )}
                    </td>
                    <td className="px-2 py-1.5 whitespace-nowrap text-scope-dim tabular-nums">
                      <span title={formatClock(s.at)}>{offsets[i] !== undefined ? formatOffset(offsets[i]!) : formatClock(s.at)}</span>
                    </td>
                    <td className={cn("px-2 py-1.5 whitespace-nowrap", toneText[tone])}>{err ? "error" : kindLabel[s.kind]}</td>
                    <td className={cn("px-2 py-1.5 whitespace-nowrap", s.kind === "tool_result" ? "text-scope-fg/80" : "text-scope-fg")}>
                      {s.kind === "tool_result" ? <span aria-hidden="true">↳ </span> : null}
                      {s.name || "—"}
                    </td>
                    <td className={cn("px-2 py-1.5 break-words", err ? "text-scope-rose" : s.kind === "note" ? "text-scope-dim" : "text-scope-fg/90")}>
                      {err ?? (stepHeadline(s) || "—")}
                    </td>
                    <td className="px-2 py-1.5 text-right text-scope-fg/85 tabular-nums">{tokens ? formatCount(tokens) : <span className="text-scope-dim">·</span>}</td>
                    <td className="py-1.5 pr-4 pl-2 text-right text-scope-dim tabular-nums">{formatCount(cum[i])}</td>
                  </tr>
                  {expandable && isOpen ? (
                    <tr id={detailId} className="border-b border-scope-rule bg-scope-raised">
                      <td colSpan={7} className="px-4 pt-1 pb-3">
                        <div className="grid gap-3 md:grid-cols-2">
                          <JsonBlock label="args" value={s.args} />
                          <JsonBlock label="result" value={s.result} />
                        </div>
                        {s.usage ? (
                          <p className="mt-2 text-xs text-scope-dim">
                            usage · {formatCount(s.usage.prompt_tokens)} in / {formatCount(s.usage.completion_tokens)} out · ${s.usage.usd.toFixed(5)}
                            {s.at ? ` · ${formatClock(s.at)} UTC` : null}
                          </p>
                        ) : null}
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
            {live ? (
              <tr aria-hidden="true">
                <td colSpan={7} className="py-2 pl-4 text-scope-dim">
                  <span className="caret text-scope-fg/70" />
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {steps.length === 0 && !live ? <p className="figure px-4 py-4 text-[0.8125rem] text-scope-dim">No steps recorded.</p> : null}
    </div>
  );
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  const text = prettyJson(value);
  return (
    <div className="min-w-0">
      <p className="mb-1 text-[0.6875rem] tracking-[0.08em] text-scope-dim uppercase">{label}</p>
      <pre className="max-h-64 overflow-auto rounded-sm bg-scope p-2.5 text-xs leading-relaxed whitespace-pre-wrap text-scope-fg/90">{text === "" || text === "null" ? "—" : text}</pre>
    </div>
  );
}
