import type { Metadata } from "next";
import Link from "next/link";
import { API_URL } from "@/lib/config";
import { CSV_EXAMPLE, CSV_REQUIRED } from "@/lib/csv";
import { container, links, textLink } from "@/lib/site";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: "Docs",
  description: "Operator token, API with curl, review CSV schema, how issues are created, and the limits of Review Radar.",
};

const TOC = [
  ["token", "Operator token"],
  ["api", "API with curl"],
  ["csv", "CSV import"],
  ["issues", "How issues are created"],
  ["limits", "Limits"],
] as const;

function Code({ children, label }: { children: string; label: string }) {
  return (
    <pre tabIndex={0} aria-label={label} className="figure overflow-x-auto rounded-md bg-scope p-4 text-[0.8125rem] leading-relaxed text-scope-fg">
      <code>{children}</code>
    </pre>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={`${id}-title`} id={id} className="scroll-mt-6 space-y-4 border-t border-border pt-10">
      <h2 id={`${id}-title`} className="text-2xl font-bold sm:text-3xl">
        {title}
      </h2>
      <div className="space-y-4 text-[0.9375rem] leading-relaxed text-foreground/85">{children}</div>
    </section>
  );
}

export default function DocsPage() {
  const api = API_URL;
  return (
    <div className={cn(container, "grid gap-10 py-10 sm:py-14 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-14")}>
      <aside className="lg:sticky lg:top-6 lg:self-start">
        <nav aria-label="On this page">
          <p className="kicker mb-3">On this page</p>
          <ul className="grid gap-2 text-sm">
            {TOC.map(([id, label]) => (
              <li key={id}>
                <a href={`#${id}`} className="text-foreground/75 hover:text-foreground">
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </aside>

      <div className="min-w-0 max-w-3xl space-y-10">
        <header className="space-y-3">
          <p className="kicker">Docs</p>
          <h1 className="text-4xl font-bold">Running Review Radar</h1>
          <p className="text-lg leading-relaxed text-foreground/80">
            One operator, one token, no accounts. Everything the web app does goes through the same JSON API, so anything here
            can be scripted. The API lives at <code className="figure text-base">{api}</code>.
          </p>
        </header>

        <Section id="token" title="Operator token">
          <p>
            The API reads a single secret, <code className="figure">OPERATOR_TOKEN</code>. Every write route needs it as a
            bearer token: adding apps, starting and processing runs, approving and rejecting proposals, importing reviews. Read
            routes for the demo app (apps, themes, reviews, proposals, runs, trajectories, the event stream) are public.
          </p>
          <p>
            In the web app, open <strong>Operator</strong> in the header and paste the token. It is kept in this browser&apos;s
            localStorage and sent only on write requests. Without it, the app is read-only and shows a{" "}
            <span className="figure">read-only</span> marker wherever a write would be.
          </p>
          <p>A missing or wrong token answers 401:</p>
          <Code label="401 error body">{`{ "detail": { "code": "unauthorized", "message": "operator token required" } }`}</Code>
        </Section>

        <Section id="api" title="API with curl">
          <p>Add an app, start a run, and drive it. The API runs on a serverless runtime with no background worker, so a run only advances while a request processes it.</p>
          <Code label="curl: add an app and run it">{`export API=${api}
export TOKEN=...   # the operator token

# add an App Store app
curl -s -X POST $API/api/apps \\
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \\
  -d '{"store":"ios","store_id":"6450012345","country":"us","github_repo":"owner/issues-repo"}'

# start a run (202, returns the run)
curl -s -X POST $API/api/apps/APP_ID/runs -H "Authorization: Bearer $TOKEN"

# process it; repeat while the answer is 202 {"resumable": true}
curl -s -X POST $API/api/runs/RUN_ID/process -H "Authorization: Bearer $TOKEN"

# follow it live (Server-Sent Events), or read the trajectory afterwards
curl -N $API/api/runs/RUN_ID/events
curl -s $API/api/runs/RUN_ID/steps`}</Code>
          <p>Review the queue and decide:</p>
          <Code label="curl: approve or reject proposals">{`# proposals waiting for a decision
curl -s "$API/api/apps/APP_ID/proposals?status=proposed"

# approve, optionally with an edited draft (issues are created on approval)
curl -s -X POST $API/api/proposals/PROPOSAL_ID/approve \\
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \\
  -d '{"draft":{"body":"Thanks for the report..."}}'

# reject with a reason (stored in the agent's memory)
curl -s -X POST $API/api/proposals/PROPOSAL_ID/reject \\
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \\
  -d '{"reason":"duplicate of #38"}'

# approved replies as CSV
curl -s "$API/api/proposals/export.csv?app=APP_ID" -o replies.csv`}</Code>
          <p>
            Errors share one shape, <code className="figure">{`{ detail: { code, message, retry_after? } }`}</code>, with codes{" "}
            <code className="figure">unauthorized</code> (401), <code className="figure">rate_limited</code> (429, with{" "}
            <code className="figure">retry_after</code> in seconds), <code className="figure">not_found</code> (404) and{" "}
            <code className="figure">invalid_input</code> (400 or 422). The web app&apos;s{" "}
            <Link href="/api/health" className={textLink} prefetch={false}>
              /api/health
            </Link>{" "}
            reports whether it can reach the API.
          </p>
        </Section>

        <Section id="csv" title="CSV import">
          <p>
            For Google Play exports or any other source, import a CSV from an app&apos;s <strong>Settings</strong> tab (or{" "}
            <code className="figure">POST /api/apps/APP_ID/import</code> as multipart form data, field{" "}
            <code className="figure">file</code>). UTF-8, comma-separated, one header row, up to 2 MB. Rows whose{" "}
            <code className="figure">store_review_id</code> is already stored are skipped.
          </p>
          <div className="overflow-x-auto rounded-md border border-border" tabIndex={0} role="region" aria-label="CSV columns, scrollable">
            <table className="w-full min-w-[30rem] text-left text-sm">
              <caption className="sr-only">CSV columns</caption>
              <thead>
                <tr className="border-b border-border text-xs text-muted-foreground">
                  <th scope="col" className="px-4 py-2 font-medium">
                    Column
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Required
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Format
                  </th>
                </tr>
              </thead>
              <tbody className="[&_td]:px-4 [&_td]:py-2 [&_tr]:border-b [&_tr]:border-border [&_tr:last-child]:border-b-0">
                {[
                  ["store_review_id", "text; unique per store"],
                  ["rating", "integer 1–5"],
                  ["body", "text"],
                  ["date", "ISO 8601 date or date-time"],
                  ["author", "text; display name only"],
                  ["title", "text"],
                  ["app_version", "text, e.g. 4.2.0"],
                  ["country", "two-letter code; defaults to the app's"],
                ].map(([col, fmt]) => (
                  <tr key={col}>
                    <td className="figure">{col}</td>
                    <td>{(CSV_REQUIRED as readonly string[]).includes(col) ? "yes" : "no"}</td>
                    <td className="text-muted-foreground">{fmt}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Code label="Example CSV">{CSV_EXAMPLE}</Code>
        </Section>

        <Section id="issues" title="How issues are created">
          <ol className="list-decimal space-y-2 pl-5">
            <li>
              During a run the agent calls <code className="figure">propose_issue(theme_id)</code>. That stores a proposal: a
              draft from the template (title, summary, evidence quotes with review ids and dates, suspected area, affected
              versions and devices, severity), its reasoning, and the guardrail results. Nothing is sent to GitHub.
            </li>
            <li>The proposal waits in the app&apos;s queue. You can edit the title and summary before approving.</li>
            <li>
              <code className="figure">POST /api/proposals/ID/approve</code> creates the issue in the app&apos;s GitHub repo
              with the API&apos;s fine-grained token (issues: write on that one repo) and stores the issue URL on the proposal.
              Approving twice is safe: the second call returns the stored result.
            </li>
            <li>
              Rejecting stores your reason as memory. The next run&apos;s <code className="figure">search_memory</code> finds it,
              so a theme you declined is not proposed again.
            </li>
          </ol>
        </Section>

        <Section id="limits" title="Limits">
          <ul className="list-disc space-y-2 pl-5">
            <li>App Store only for fetching (public RSS feed, about 500 recent reviews per country). Google Play through CSV import.</li>
            <li>Replies are exported, not posted: the stores need your developer credentials.</li>
            <li>One run: at most 24 agent iterations, a 200-second deadline, and a $0.10 budget. Processing requests last up to about 250 s and resume where they stopped.</li>
            <li>One operator token; no accounts, roles or audit per person. Every decision is still recorded with its time.</li>
            <li>Policies are a text box. The deterministic guardrails cover length (350 characters for App Store replies), banned phrases, links, promised dates, refunds and personal data.</li>
          </ul>
          <p>
            Questions or bugs: open an issue on{" "}
            <a href={links.repo} className={textLink}>
              GitHub
            </a>
            .
          </p>
        </Section>
      </div>
    </div>
  );
}
