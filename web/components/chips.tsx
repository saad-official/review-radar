import { cn } from "@/lib/utils";
import type { Category, ProposalKind, ProposalStatus, RunStatus, ThemeKind, ThemeStatus } from "@/lib/api";

export type Tone = "radar" | "amber" | "rose" | "neutral" | "outline";

const toneClass: Record<Tone, string> = {
  radar: "border-radar/35 bg-radar-wash text-radar-ink",
  amber: "border-amber/45 bg-amber-wash text-amber-ink",
  rose: "border-rose/35 bg-rose-wash text-rose-ink",
  neutral: "border-border bg-muted text-foreground/80",
  outline: "border-border bg-transparent text-muted-foreground",
};

const dotClass: Record<Tone, string> = {
  radar: "bg-radar",
  amber: "bg-amber",
  rose: "bg-rose",
  neutral: "bg-slate",
  outline: "bg-slate/60",
};

/** Small mono chip. Colour is never the only signal: the label always says it. */
export function Chip({
  tone = "neutral",
  dot = false,
  className,
  children,
  title,
}: {
  tone?: Tone;
  dot?: boolean;
  className?: string;
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "figure inline-flex h-6 max-w-full shrink-0 items-center gap-1.5 rounded-sm border px-2 text-[0.75rem] leading-none font-medium whitespace-nowrap",
        toneClass[tone],
        className,
      )}
    >
      {dot ? <span aria-hidden="true" className={cn("size-1.5 shrink-0 rounded-full", dotClass[tone])} /> : null}
      <span className="truncate">{children}</span>
    </span>
  );
}

export const proposalTone: Record<ProposalStatus, Tone> = {
  proposed: "amber",
  approved: "radar",
  executed: "radar",
  rejected: "rose",
  failed: "rose",
};

export function ProposalStatusChip({ status }: { status: ProposalStatus }) {
  return (
    <Chip tone={proposalTone[status]} dot>
      {status === "proposed" ? "waiting" : status}
    </Chip>
  );
}

export function KindChip({ kind }: { kind: ProposalKind }) {
  return <Chip tone="neutral">{kind === "issue" ? "issue" : "reply"}</Chip>;
}

export function runTone(status: RunStatus): Tone {
  if (status === "done") return "radar";
  if (status === "failed") return "rose";
  if (status === "queued") return "outline";
  return "amber";
}

export function RunStatusChip({ status }: { status: RunStatus }) {
  return (
    <Chip tone={runTone(status)} dot>
      {status}
    </Chip>
  );
}

export function ThemeKindChip({ kind }: { kind: ThemeKind | Category }) {
  const tone: Tone = kind === "bug" || kind === "billing" ? "neutral" : kind === "praise" ? "outline" : "neutral";
  return <Chip tone={tone}>{kind}</Chip>;
}

export function ThemeStatusChip({ status }: { status: ThemeStatus }) {
  if (status === "open") return null;
  return <Chip tone="outline">{status}</Chip>;
}

/** Severity 1–5. 4 and 5 are rose (the spec's "high severity"), 3 amber, lower neutral. */
export function SeverityChip({ severity }: { severity: number | undefined }) {
  if (severity === undefined) return null;
  const tone: Tone = severity >= 4 ? "rose" : severity === 3 ? "amber" : "outline";
  return (
    <Chip tone={tone} title={`Severity ${severity} of 5`}>
      sev {severity}
    </Chip>
  );
}

/** Small "read-only" marker shown where a write action would be, when no token is set. */
export function ReadOnlyChip({ className }: { className?: string }) {
  return (
    <Chip tone="outline" className={className}>
      read-only
    </Chip>
  );
}
