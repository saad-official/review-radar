import Link from "next/link";

/** A quarter-scope glyph (two range rings, a sweep line, one blip) and the name. */
export function RadarGlyph({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 20 20" className={className} aria-hidden="true" focusable="false">
      <path d="M2 18 A16 16 0 0 1 18 2" fill="none" stroke="currentColor" strokeOpacity="0.35" strokeWidth="1.5" />
      <path d="M2 18 A9 9 0 0 1 11 9" fill="none" stroke="currentColor" strokeOpacity="0.55" strokeWidth="1.5" />
      <path d="M2 18 L14.5 5.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="13.6" cy="11.2" r="1.9" className="fill-radar" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <Link href="/" className="inline-flex items-center gap-2 rounded-sm" aria-label="Review Radar, home">
      <RadarGlyph className="size-5 text-foreground" />
      <span aria-hidden="true" className="font-heading text-[1.05rem] font-bold tracking-[-0.03em]">
        review<span className="font-medium text-slate">radar</span>
      </span>
    </Link>
  );
}
