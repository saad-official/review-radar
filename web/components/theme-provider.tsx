"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";

/** Light and dark through the `.dark` class; follows the OS until the visitor picks. */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      {children}
    </NextThemesProvider>
  );
}
