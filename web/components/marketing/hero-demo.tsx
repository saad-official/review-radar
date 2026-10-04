"use client";

import { useState } from "react";
import { CheckIcon, RotateCcwIcon } from "lucide-react";
import { Chip, SeverityChip, ThemeKindChip } from "@/components/chips";
import { EvidenceList } from "@/components/review/evidence-list";
import { Sparkline } from "@/components/sparkline";
import type { Proposal, Theme } from "@/lib/api";

/**
 * Static demo for the landing page: one theme from the fixture with its evidence,
 * and the issue the agent proposed beside it. The Approve control only flips local
 * state; nothing is sent anywhere.
 */
export function HeroDemo({ theme, proposal }: { theme: Theme; proposal: Proposal }) {
  const [approved, setApproved] = useState(false);
  return (
    <div className="grid gap-4 lg:grid-cols-2 lg:gap-0">
      <section aria-labelledby="demo-theme" className="rounded-lg border border-border bg-card p-5 shadow-panel lg:rounded-r-none">
        <div className="flex items-center justify-between gap-3">
          <p className="kicker">Theme · rank 01</p>
          <span className="figure text-xs text-muted-foreground">{theme.id}</span>
        </div>
        <h2 id="demo-theme" className="mt-3 text-xl leading-snug font-semibold">
          {theme.title}
        </h2>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <ThemeKindChip kind={theme.kind} />
          <span className="figure text-sm">{theme.review_count} reviews</span>
          <Sparkline values={theme.sentiment} />
        </div>
        <p className="kicker mt-5 mb-2">Evidence</p>
        <EvidenceList evidence={proposal.draft.evidence.slice(0, 3)} />
      </section>

      <section
        aria-labelledby="demo-issue"
        className="relative overflow-hidden rounded-lg border border-border bg-card p-5 shadow-panel lg:rounded-l-none lg:border-l-0"
      >
        <span aria-hidden="true" className={approved ? "absolute inset-y-0 left-0 w-1 bg-radar" : "absolute inset-y-0 left-0 w-1 bg-amber"} />
        <div className="flex flex-wrap items-center gap-2">
          <Chip>issue</Chip>
          <Chip tone={approved ? "radar" : "amber"} dot>
            {approved ? "approved" : "waiting"}
          </Chip>
          <SeverityChip severity={proposal.draft.severity} />
          <span className="figure ml-auto text-xs text-muted-foreground">{proposal.id}</span>
        </div>
        <p className="kicker mt-4">Proposed GitHub issue</p>
        <h2 id="demo-issue" className="mt-1.5 text-lg leading-snug font-semibold">
          {proposal.draft.title}
        </h2>
        <p className="mt-2 line-clamp-4 text-sm leading-relaxed text-foreground/85">{proposal.draft.summary}</p>
        <dl className="figure mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
          <dt className="text-muted-foreground">versions</dt>
          <dd>{proposal.draft.affected_versions.join(", ")}</dd>
          <dt className="text-muted-foreground">devices</dt>
          <dd>{proposal.draft.devices.join(", ")}</dd>
        </dl>
        <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-border pt-4">
          {approved ? (
            <>
              <p className="text-sm text-radar-ink" role="status">
                Approved. In the real app this creates the issue in your repo.
              </p>
              <button
                type="button"
                onClick={() => setApproved(false)}
                className="figure ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground underline underline-offset-4 hover:text-foreground"
              >
                <RotateCcwIcon className="size-3" aria-hidden="true" />
                reset demo
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setApproved(true)}
                className="inline-flex h-9 items-center gap-1.5 rounded-md bg-radar-ink px-3.5 text-sm font-semibold text-white hover:bg-radar-ink/90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground dark:bg-radar dark:text-[oklch(0.18_0.02_160)]"
              >
                <CheckIcon className="size-4" aria-hidden="true" />
                Approve issue
              </button>
              <span className="text-xs text-muted-foreground">Demo: nothing is sent.</span>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
