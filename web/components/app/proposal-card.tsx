"use client";

import { useState } from "react";
import { CheckIcon, ExternalLinkIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Chip, KindChip, ProposalStatusChip, SeverityChip } from "@/components/chips";
import { ReadOnlyNotice } from "@/components/operator/operator-provider";
import { EvidenceList } from "@/components/review/evidence-list";
import { useOperatorToken } from "@/hooks/use-operator-token";
import type { Guardrails, Proposal } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ApproveDialog, REPLY_LIMIT, RejectDialog } from "./decide-dialogs";

function GuardrailList({ guardrails }: { guardrails: Guardrails }) {
  if (guardrails.checks.length === 0) {
    return <p className="figure text-xs text-muted-foreground">no guardrail checks recorded</p>;
  }
  return (
    <ul className="grid gap-1 sm:grid-cols-2">
      {guardrails.checks.map((c) => (
        <li key={c.name} className="figure flex min-w-0 items-baseline gap-2 text-xs">
          <span aria-hidden="true" className={c.ok ? "text-radar-ink" : "text-rose-ink"}>
            {c.ok ? "✓" : "✗"}
          </span>
          <span className="sr-only">{c.ok ? "passed:" : "failed:"}</span>
          <span className="shrink-0 text-foreground/85">{c.name}</span>
          {c.note ? <span className="min-w-0 truncate text-muted-foreground" title={c.note}>{c.note}</span> : null}
        </li>
      ))}
    </ul>
  );
}

/**
 * One proposal in the approval queue: what the agent wants to write, the evidence
 * it is based on, why, and what the deterministic guardrails said. Approve opens a
 * dialog where the draft can be edited first; Reject asks for a reason.
 */
export function ProposalCard({
  proposal,
  themeTitle,
  onDecided,
  headingLevel = 3,
}: {
  proposal: Proposal;
  themeTitle?: string;
  onDecided: (p: Proposal) => void;
  headingLevel?: 2 | 3;
}) {
  const token = useOperatorToken();
  const [approveOpen, setApproveOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const { draft, guardrails } = proposal;
  const isIssue = proposal.kind === "issue";
  const waiting = proposal.status === "proposed";
  const H = headingLevel === 2 ? "h2" : "h3";
  const headingId = `proposal-${proposal.id}`;
  const replyLength = draft.body?.trim().length ?? 0;

  return (
    <article
      aria-labelledby={headingId}
      className={cn(
        "relative overflow-hidden rounded-lg border bg-card shadow-panel",
        waiting ? "border-border" : "border-border/70 opacity-95",
      )}
    >
      {/* Left edge: the status colour, plus the status chip in text. */}
      <span
        aria-hidden="true"
        className={cn(
          "absolute inset-y-0 left-0 w-1",
          waiting ? "bg-amber" : proposal.status === "rejected" || proposal.status === "failed" ? "bg-rose" : "bg-radar",
        )}
      />
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-3 pl-6">
        <KindChip kind={proposal.kind} />
        <ProposalStatusChip status={proposal.status} />
        {isIssue ? <SeverityChip severity={draft.severity} /> : null}
        {!guardrails.passed ? <Chip tone="rose">guardrails failed</Chip> : null}
        {themeTitle ? <span className="min-w-0 truncate text-sm text-muted-foreground">theme · {themeTitle}</span> : null}
        <span className="figure ml-auto text-xs text-muted-foreground">{proposal.id}</span>
      </div>

      <div className="grid gap-6 px-5 py-5 pl-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-3">
          {isIssue ? (
            <>
              <p className="kicker">GitHub issue draft</p>
              <H id={headingId} className="text-lg leading-snug font-semibold sm:text-xl">
                {draft.title ?? "Untitled issue"}
              </H>
              {draft.summary ? <p className="text-[0.9375rem] leading-relaxed text-foreground/85">{draft.summary}</p> : null}
              <dl className="figure grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
                {draft.affected_versions.length > 0 ? (
                  <>
                    <dt className="text-muted-foreground">versions</dt>
                    <dd>{draft.affected_versions.join(", ")}</dd>
                  </>
                ) : null}
                {draft.devices.length > 0 ? (
                  <>
                    <dt className="text-muted-foreground">devices</dt>
                    <dd>{draft.devices.join(", ")}</dd>
                  </>
                ) : null}
                {draft.suspected_area ? (
                  <>
                    <dt className="text-muted-foreground">suspected</dt>
                    <dd className="font-sans">{draft.suspected_area}</dd>
                  </>
                ) : null}
              </dl>
            </>
          ) : (
            <>
              <p className="kicker">
                Reply draft{proposal.review_id ? <span className="normal-case"> · to {proposal.review_id}</span> : null}
              </p>
              <H id={headingId} className="sr-only">
                Reply to {proposal.review_id ?? "a review"}
              </H>
              <p className="rounded-md rounded-tl-none border border-border bg-muted/60 px-4 py-3 text-[0.9375rem] leading-relaxed">
                {draft.body ?? "No reply text."}
              </p>
              <p className={cn("figure text-xs", replyLength > REPLY_LIMIT ? "text-rose-ink" : "text-muted-foreground")}>
                {replyLength} / {REPLY_LIMIT} characters
              </p>
            </>
          )}
        </div>

        <div className="min-w-0 space-y-3">
          <p className="kicker">Evidence</p>
          <EvidenceList evidence={draft.evidence} />
        </div>
      </div>

      <div className="grid gap-4 border-t border-border bg-muted/30 px-5 py-4 pl-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <div className="min-w-0">
          <p className="kicker mb-1.5">Why the agent proposed it</p>
          <p className="text-sm leading-relaxed text-foreground/85">{proposal.reasoning ?? "No reasoning recorded."}</p>
        </div>
        <div className="min-w-0">
          <p className="kicker mb-1.5">
            Guardrails · <span className={guardrails.passed ? "text-radar-ink" : "text-rose-ink"}>{guardrails.passed ? "passed" : "failed"}</span>
          </p>
          <GuardrailList guardrails={guardrails} />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-border px-5 py-3 pl-6">
        {waiting ? (
          token ? (
            <>
              <Button
                type="button"
                className="h-9 bg-radar-ink px-3.5 text-white hover:bg-radar-ink/90 dark:bg-radar dark:text-[oklch(0.18_0.02_160)]"
                onClick={() => setApproveOpen(true)}
              >
                <CheckIcon aria-hidden="true" />
                {isIssue ? "Approve issue" : "Approve reply"}
              </Button>
              <Button type="button" variant="outline" className="h-9 px-3.5" onClick={() => setRejectOpen(true)}>
                <XIcon aria-hidden="true" />
                Reject
              </Button>
              <span className="text-xs text-muted-foreground">You can edit the draft before it is approved.</span>
            </>
          ) : (
            <>
              <Button type="button" className="h-9 px-3.5" disabled>
                Approve
              </Button>
              <Button type="button" variant="outline" className="h-9 px-3.5" disabled>
                Reject
              </Button>
              <ReadOnlyNotice />
            </>
          )
        ) : (
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <span className="text-muted-foreground">
              {proposal.status} {proposal.decided_at ? `· ${formatDateTime(proposal.decided_at)}` : null}
            </span>
            {proposal.reason ? <span className="text-muted-foreground">reason: &ldquo;{proposal.reason}&rdquo;</span> : null}
            {proposal.result.url ? (
              <a href={proposal.result.url} className="inline-flex items-center gap-1 font-medium underline underline-offset-4" target="_blank" rel="noreferrer">
                {proposal.result.number ? `Issue #${proposal.result.number}` : "Open result"}
                <ExternalLinkIcon className="size-3.5" aria-hidden="true" />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            ) : null}
            {proposal.result.error ? <span className="text-rose-ink">{proposal.result.error}</span> : null}
          </p>
        )}
      </div>

      <ApproveDialog proposal={proposal} open={approveOpen} onOpenChange={setApproveOpen} onDecided={onDecided} />
      <RejectDialog proposal={proposal} open={rejectOpen} onOpenChange={setRejectOpen} onDecided={onDecided} />
    </article>
  );
}
