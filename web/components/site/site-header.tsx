import Link from "next/link";
import { cn } from "@/lib/utils";
import { container, links } from "@/lib/site";
import { API_MOCK } from "@/lib/config";
import { ThemeToggle } from "@/components/theme-toggle";
import { OperatorButton } from "@/components/operator/operator-provider";
import { Wordmark } from "./wordmark";

const navLink = "rounded-sm text-sm font-medium text-foreground/75 hover:text-foreground motion-safe:transition-colors";

function NavLinks({ className }: { className?: string }) {
  return (
    <ul className={className}>
      <li>
        <Link href={links.apps} className={navLink}>
          Apps
        </Link>
      </li>
      <li>
        <Link href={links.howItWorks} className={navLink}>
          How it works
        </Link>
      </li>
      <li>
        <Link href={links.docs} className={navLink}>
          Docs
        </Link>
      </li>
      <li>
        <a href={links.repo} className={navLink}>
          GitHub
        </a>
      </li>
    </ul>
  );
}

export function SiteHeader() {
  return (
    <header className="border-b border-border bg-background">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground focus:not-sr-only focus:absolute focus:top-3 focus:left-3"
      >
        Skip to content
      </a>
      {API_MOCK ? (
        <p className="figure border-b border-amber/40 bg-amber-wash py-1 text-center text-xs text-amber-ink">
          mock mode: fixture data, no API calls
        </p>
      ) : null}
      <div className={cn(container, "flex h-14 items-center gap-6 sm:h-16 sm:gap-8")}>
        <Wordmark />
        <nav aria-label="Main" className="hidden md:block">
          <NavLinks className="flex items-center gap-7" />
        </nav>
        <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
          <OperatorButton />
          <ThemeToggle />
          <Link
            href={links.apps}
            className="hidden h-9 items-center rounded-md bg-primary px-3.5 text-sm font-semibold text-primary-foreground hover:bg-primary/88 motion-safe:transition-colors sm:inline-flex"
          >
            Open the queue
          </Link>
        </div>
      </div>
      {/* Phones: the same links on their own row, so no menu button is needed. */}
      <nav aria-label="Main" className="border-t border-border md:hidden">
        <NavLinks className={cn(container, "flex h-11 items-center gap-5 overflow-x-auto")} />
      </nav>
    </header>
  );
}
