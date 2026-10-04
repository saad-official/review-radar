import type { Metadata, Viewport } from "next";
import { DM_Mono, Inter, Space_Grotesk } from "next/font/google";
import { OperatorProvider } from "@/components/operator/operator-provider";
import { SiteFooter } from "@/components/site/site-footer";
import { SiteHeader } from "@/components/site/site-header";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import "./globals.css";

// Space Grotesk and Inter are variable fonts: no weight arrays. DM Mono is static, so it lists its two weights.
const spaceGrotesk = Space_Grotesk({ variable: "--font-space-grotesk", subsets: ["latin"], display: "swap" });
const inter = Inter({ variable: "--font-inter", subsets: ["latin"], display: "swap" });
const dmMono = DM_Mono({ variable: "--font-dm-mono", subsets: ["latin"], weight: ["400", "500"], display: "swap" });

const appUrl = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";

export const metadata: Metadata = {
  metadataBase: new URL(appUrl),
  title: {
    default: "Review Radar: app-store reviews in, approved issues and replies out",
    template: "%s · Review Radar",
  },
  description:
    "Review Radar reads your app's store reviews, groups them into themes, and drafts GitHub issues and replies with the evidence attached. Nothing is written until you approve it.",
  openGraph: {
    title: "Review Radar",
    description: "An agent that triages app-store reviews into themes, issue drafts and replies, and waits for your approval before writing anything.",
    type: "website",
    url: appUrl,
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f6f8" },
    { media: "(prefers-color-scheme: dark)", color: "#14171b" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${spaceGrotesk.variable} ${inter.variable} ${dmMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="flex min-h-full flex-col overflow-x-clip bg-background font-sans text-foreground">
        <ThemeProvider>
          <TooltipProvider delayDuration={200}>
            <OperatorProvider>
              <SiteHeader />
              <main id="main" tabIndex={-1} className="flex-1 outline-none">
                {children}
              </main>
              <SiteFooter />
              <Toaster position="bottom-right" closeButton />
            </OperatorProvider>
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
