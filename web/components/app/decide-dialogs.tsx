"use client";

import { useId, useState } from "react";
import { Loader2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useOperator } from "@/components/operator/operator-provider";
import { approveProposal, rejectProposal, type Proposal } from "@/lib/api";
import { asApiError } from "@/lib/errors";
import { cn } from "@/lib/utils";

export const REPLY_LIMIT = 350;

export function ApproveDialog({
  proposal,
  open,
  onOpenChange,
  onDecided,
}: {
  proposal: Proposal;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDecided: (p: Proposal) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-xl">
        {open ? <ApproveForm proposal={proposal} onClose={() => onOpenChange(false)} onDecided={onDecided} /> : null}
      </DialogContent>
    </Dialog>
  );
}

function ApproveForm({ proposal, onClose, onDecided }: { proposal: Proposal; onClose: () => void; onDecided: (p: Proposal) => void }) {
  const { reportError } = useOperator();
  const isIssue = proposal.kind === "issue";
  const [title, setTitle] = useState(proposal.draft.title ?? "");
  const [summary, setSummary] = useState(proposal.draft.summary ?? "");
  const [body, setBody] = useState(proposal.draft.body ?? "");
  const [busy, setBusy] = useState(false);
  const titleId = useId();
  const summaryId = useId();
  const bodyId = useId();
  const countId = useId();

  const edited: Record<string, string> = {};
  if (isIssue) {
    if (title.trim() !== (proposal.draft.title ?? "").trim()) edited.title = title.trim();
    if (summary.trim() !== (proposal.draft.summary ?? "").trim()) edited.summary = summary.trim();
  } else if (body.trim() !== (proposal.draft.body ?? "").trim()) edited.body = body.trim();
  const isEdited = Object.keys(edited).length > 0;
  const overLimit = !isIssue && body.trim().length > REPLY_LIMIT;
  const empty = isIssue ? title.trim() === "" : body.trim() === "";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy || overLimit || empty) return;
    setBusy(true);
    try {
      const updated = await approveProposal(proposal.id, isEdited ? edited : undefined);
      onDecided(updated);
      onClose();
    } catch (err) {
      reportError(asApiError(err), "Approve");
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="grid gap-5">
      <DialogHeader>
        <p className="kicker">{proposal.id}</p>
        <DialogTitle className="text-xl">{isIssue ? "Approve and create the issue" : "Approve this reply"}</DialogTitle>
        <DialogDescription>
          {isIssue
            ? "Approving creates a GitHub issue in the app's connected repository with this title, summary and the evidence quotes. Edit first if you like."
            : "Approving adds this reply to the export (CSV). Store APIs need developer credentials, so Review Radar does not post replies itself."}
        </DialogDescription>
      </DialogHeader>

      {isIssue ? (
        <>
          <div className="grid gap-2">
            <label htmlFor={titleId} className="text-sm font-medium">
              Issue title
            </label>
            <Input id={titleId} value={title} onChange={(e) => setTitle(e.target.value)} className="h-10" required />
          </div>
          <div className="grid gap-2">
            <label htmlFor={summaryId} className="text-sm font-medium">
              Summary
            </label>
            <Textarea id={summaryId} value={summary} onChange={(e) => setSummary(e.target.value)} rows={6} className="leading-relaxed" />
            <p className="text-xs text-muted-foreground">Evidence quotes, affected versions and severity are added below the summary automatically.</p>
          </div>
        </>
      ) : (
        <div className="grid gap-2">
          <div className="flex items-baseline justify-between gap-3">
            <label htmlFor={bodyId} className="text-sm font-medium">
              Reply
            </label>
            <span id={countId} className={cn("figure text-xs tabular-nums", overLimit ? "text-rose-ink" : "text-muted-foreground")}>
              {body.trim().length} / {REPLY_LIMIT}
            </span>
          </div>
          <Textarea
            id={bodyId}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={6}
            aria-describedby={countId}
            aria-invalid={overLimit || undefined}
            className="leading-relaxed"
          />
          {overLimit ? (
            <p className="text-xs text-rose-ink" role="alert">
              App Store replies must stay under {REPLY_LIMIT} characters.
            </p>
          ) : null}
        </div>
      )}

      <DialogFooter className="gap-2 sm:justify-between">
        <span className="figure self-center text-xs text-muted-foreground">{isEdited ? "edited: your version is sent" : "unchanged: the agent's draft is sent"}</span>
        <div className="flex flex-col-reverse gap-2 sm:flex-row">
          <Button type="button" variant="outline" className="h-10" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" className="h-10 bg-radar-ink text-white hover:bg-radar-ink/90 dark:bg-radar dark:text-[oklch(0.18_0.02_160)]" disabled={busy || overLimit || empty}>
            {busy ? <Loader2Icon className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : null}
            {isIssue ? "Approve and create issue" : "Approve reply"}
          </Button>
        </div>
      </DialogFooter>
    </form>
  );
}

const QUICK_REASONS = ["duplicate of an existing issue", "not a bug", "tone is wrong", "won't fix", "needs more evidence"];

export function RejectDialog({
  proposal,
  open,
  onOpenChange,
  onDecided,
}: {
  proposal: Proposal;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDecided: (p: Proposal) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">{open ? <RejectForm proposal={proposal} onClose={() => onOpenChange(false)} onDecided={onDecided} /> : null}</DialogContent>
    </Dialog>
  );
}

function RejectForm({ proposal, onClose, onDecided }: { proposal: Proposal; onClose: () => void; onDecided: (p: Proposal) => void }) {
  const { reportError } = useOperator();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const reasonId = useId();
  const hintId = useId();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      const updated = await rejectProposal(proposal.id, reason);
      onDecided(updated);
      onClose();
    } catch (err) {
      reportError(asApiError(err), "Reject");
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="grid gap-5">
      <DialogHeader>
        <p className="kicker">{proposal.id}</p>
        <DialogTitle className="text-xl">Reject this {proposal.kind}</DialogTitle>
        <DialogDescription>Nothing is written. The reason is stored in the agent&apos;s memory, so it will not propose the same thing again.</DialogDescription>
      </DialogHeader>
      <div className="grid gap-2">
        <label htmlFor={reasonId} className="text-sm font-medium">
          Reason <span className="font-normal text-muted-foreground">(recommended)</span>
        </label>
        <Textarea id={reasonId} value={reason} onChange={(e) => setReason(e.target.value)} rows={3} aria-describedby={hintId} placeholder="duplicate of #38" />
        <div id={hintId} className="flex flex-wrap gap-1.5" role="group" aria-label="Common reasons">
          {QUICK_REASONS.map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setReason(r)}
              className="figure rounded-sm border border-border px-2 py-1 text-xs text-foreground/80 hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground"
            >
              {r}
            </button>
          ))}
        </div>
      </div>
      <DialogFooter className="gap-2">
        <Button type="button" variant="outline" className="h-10" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" variant="destructive" className="h-10" disabled={busy}>
          {busy ? <Loader2Icon className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : null}
          Reject
        </Button>
      </DialogFooter>
    </form>
  );
}
