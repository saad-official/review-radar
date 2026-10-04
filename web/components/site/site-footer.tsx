import Link from "next/link";
import { cn } from "@/lib/utils";
import { container, links, textLink } from "@/lib/site";
import { Wordmark } from "./wordmark";

export function SiteFooter() {
  return (
    <footer className="mt-auto border-t border-border">
      <div className={cn(container, "grid gap-8 py-10 sm:grid-cols-[1fr_auto] sm:items-start")}>
        <div className="space-y-3">
          <Wordmark />
          <p className="max-w-md text-sm text-muted-foreground">
            App-store reviews in; themes, issue drafts and replies out. The agent proposes, a person approves, and only
            then does anything get written.
          </p>
        </div>
        <nav aria-label="Footer">
          <ul className="grid gap-2.5 text-sm">
            <li>
              <a href={links.repo} className={textLink}>
                Source on GitHub
              </a>
            </li>
            <li>
              <a href={links.journey} className={textLink}>
                AI Engineering Journey
              </a>
            </li>
            <li>
              <a href={links.series} className={textLink}>
                Vibe Build Series
              </a>
            </li>
            <li>
              <Link href={links.docs} className={textLink}>
                Docs: token, API, CSV
              </Link>
            </li>
          </ul>
        </nav>
      </div>
      <div className="border-t border-border">
        <p className={cn(container, "figure py-4 text-xs text-muted-foreground")}>
          fetch → extract → cluster → propose → you approve → execute
        </p>
      </div>
    </footer>
  );
}
