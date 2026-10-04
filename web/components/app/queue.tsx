"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorBox } from "@/components/error-box";
import type { Resource } from "@/hooks/use-resource";
import type { Proposal } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ProposalCard } from "./proposal-card";

type Filter = "all" | "issue" | "reply";

/**
 * The approval queue: proposals waiting for a person. Decisions made here stay
 * on screen (as decided cards, below the waiting ones) until the next reload.
 */
export function Queue({
  proposals,
  themeTitles,
  onChanged,
}: {
  proposals: Resource<Proposal[]>;
  themeTitles: Map<string, string>;
  onChanged: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const all = proposals.data ?? [];
  const waiting = all.filter((p) => p.status === "proposed");
  const decided = all.filter((p) => p.status !== "proposed");
  const shown = (list: Proposal[]) => (filter === "all" ? list : list.filter((p) => p.kind === filter));

  const onDecided = (p: Proposal) => {
    proposals.mutate((prev) => prev?.map((x) => (x.id === p.id ? p : x)));
    onChanged();
    if (p.status === "executed" && p.result.url) {
      toast.success(p.result.number ? `Issue #${p.result.number} created` : "Issue created", {
        description: p.draft.title,
        action: { label: "Open", onClick: () => window.open(p.result.url, "_blank", "noopener,noreferrer") },
      });
    } else if (p.status === "approved") {
      toast.success(p.kind === "reply" ? "Reply approved: it is in the export now" : "Approved");
    } else if (p.status === "rejected") {
      toast("Rejected. The reason is in the agent's memory.");
    } else if (p.status === "failed") {
      toast.error("Approved, but executing it failed", { description: p.result.error });
    }
  };

  if (proposals.error) return <ErrorBox error={proposals.error} />;
  if (!proposals.data) {
    return (
      <div className="grid gap-5" aria-busy="true">
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  const counts = { all: waiting.length, issue: waiting.filter((p) => p.kind === "issue").length, reply: waiting.filter((p) => p.kind === "reply").length };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-md border border-border p-0.5" role="group" aria-label="Filter the queue">
          {(["all", "issue", "reply"] as const).map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={filter === f}
              onClick={() => setFilter(f)}
              className={cn(
                "figure h-8 rounded-sm px-3 text-xs focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-foreground",
                filter === f ? "bg-foreground text-background" : "text-foreground/75 hover:bg-muted",
              )}
            >
              {f === "all" ? "all" : f === "issue" ? "issues" : "replies"} · {counts[f]}
            </button>
          ))}
        </div>
        <p className="text-sm text-muted-foreground">Nothing is written until you approve it.</p>
      </div>

      {shown(waiting).length === 0 ? (
        <EmptyState title={waiting.length === 0 ? "Queue clear" : "Nothing of this kind waiting"}>
          {waiting.length === 0 ? "Every proposal has been decided. New ones arrive with the next run." : "Switch the filter to see the rest of the queue."}
        </EmptyState>
      ) : (
        <ol className="grid gap-5" aria-label="Proposals waiting for approval">
          {shown(waiting).map((p) => (
            <li key={p.id}>
              <ProposalCard proposal={p} themeTitle={p.theme_id ? themeTitles.get(p.theme_id) : undefined} onDecided={onDecided} />
            </li>
          ))}
        </ol>
      )}

      {shown(decided).length > 0 ? (
        <section aria-labelledby="decided-title" className="space-y-4 pt-4">
          <h3 id="decided-title" className="kicker">
            Decided this session
          </h3>
          <ol className="grid gap-5">
            {shown(decided).map((p) => (
              <li key={p.id}>
                <ProposalCard proposal={p} themeTitle={p.theme_id ? themeTitles.get(p.theme_id) : undefined} onDecided={onDecided} />
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </div>
  );
}
