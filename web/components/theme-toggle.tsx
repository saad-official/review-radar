"use client";

import { MoonIcon, SunIcon } from "lucide-react";
import { useTheme } from "next-themes";

/**
 * Both icons render on the server; CSS shows the right one, so there is no
 * hydration flash and no mounted-state effect.
 */
export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  return (
    <button
      type="button"
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
      className="grid size-9 place-items-center rounded-md text-foreground/75 hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground"
      aria-label="Toggle dark mode"
      title="Toggle dark mode"
    >
      <MoonIcon className="size-4 dark:hidden" aria-hidden="true" />
      <SunIcon className="hidden size-4 dark:block" aria-hidden="true" />
    </button>
  );
}
