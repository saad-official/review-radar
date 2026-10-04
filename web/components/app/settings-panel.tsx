"use client";

import { useId, useState } from "react";
import { DownloadIcon, Loader2Icon, UploadIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ReadOnlyNotice, useOperator } from "@/components/operator/operator-provider";
import { useOperatorToken } from "@/hooks/use-operator-token";
import { exportUrl, importReviews, listProposals, updateApp, type App } from "@/lib/api";
import { API_MOCK } from "@/lib/config";
import { CSV_COLUMNS, CSV_MAX_BYTES, CSV_REQUIRED, checkCsvHeader } from "@/lib/csv";
import { asApiError } from "@/lib/errors";

const REPO_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\/[A-Za-z0-9._-]{1,100}$/;

export function SettingsPanel({ app, onSaved }: { app: App; onSaved: (app: App) => void }) {
  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <AppSettingsForm app={app} onSaved={onSaved} />
      <div className="space-y-10">
        <ImportCsv appId={app.id} />
        <ExportReplies appId={app.id} />
      </div>
    </div>
  );
}

function AppSettingsForm({ app, onSaved }: { app: App; onSaved: (app: App) => void }) {
  const token = useOperatorToken();
  const { reportError } = useOperator();
  const [policy, setPolicy] = useState(app.policy ?? "");
  const [repo, setRepo] = useState(app.github_repo ?? "");
  const [busy, setBusy] = useState(false);
  const policyId = useId();
  const repoId = useId();
  const repoHint = useId();
  const repoInvalid = repo.trim() !== "" && !REPO_RE.test(repo.trim());
  const dirty = policy !== (app.policy ?? "") || repo !== (app.github_repo ?? "");

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy || repoInvalid || !dirty) return;
    setBusy(true);
    try {
      const updated = await updateApp(app.id, { policy: policy.trim() || null, github_repo: repo.trim() || null });
      onSaved(updated);
      toast.success("Settings saved. The next run uses them.");
    } catch (err) {
      reportError(asApiError(err), "Save settings");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="settings-title" className="space-y-5">
      <div>
        <h3 id="settings-title" className="text-xl font-semibold">
          Reply policy and repository
        </h3>
        <p className="text-sm text-muted-foreground">The policy goes into every drafting prompt; the guardrails check the length, links and banned phrases in code.</p>
      </div>
      <form onSubmit={save} className="space-y-5">
        <div className="grid gap-2">
          <label htmlFor={policyId} className="text-sm font-medium">
            Policy
          </label>
          <Textarea id={policyId} value={policy} onChange={(e) => setPolicy(e.target.value)} rows={8} className="figure text-[0.8125rem] leading-relaxed" readOnly={!token} />
        </div>
        <div className="grid gap-2">
          <label htmlFor={repoId} className="text-sm font-medium">
            GitHub repository for issues
          </label>
          <Input
            id={repoId}
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="owner/name"
            className="figure h-10"
            aria-describedby={repoHint}
            aria-invalid={repoInvalid || undefined}
            readOnly={!token}
            spellCheck={false}
            autoCapitalize="off"
          />
          <p id={repoHint} className={repoInvalid ? "text-xs text-rose-ink" : "text-xs text-muted-foreground"}>
            {repoInvalid ? "Use owner/name, for example saad-official/review-radar-demo-issues." : "Approved issues are created here with the API's GitHub token. Leave empty to approve without creating issues."}
          </p>
        </div>
        {token ? (
          <Button type="submit" className="h-10" disabled={busy || repoInvalid || !dirty}>
            {busy ? <Loader2Icon className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : null}
            Save settings
          </Button>
        ) : (
          <ReadOnlyNotice label="read-only settings" />
        )}
      </form>
    </section>
  );
}

function ImportCsv({ appId }: { appId: string }) {
  const token = useOperatorToken();
  const { reportError } = useOperator();
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [inputKey, setInputKey] = useState(0);
  const fileId = useId();
  const hintId = useId();

  const pick = async (f: File | null) => {
    setFile(null);
    setProblem(null);
    if (!f) return;
    if (f.size > CSV_MAX_BYTES) {
      setProblem("The file is larger than 2 MB. Split it and import the parts.");
      return;
    }
    const head = await f.slice(0, 4096).text();
    const check = checkCsvHeader(head);
    if (!check.ok) {
      setProblem(check.error);
      return;
    }
    setFile(f);
  };

  const upload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || busy) return;
    setBusy(true);
    try {
      const result = await importReviews(appId, file);
      toast.success(`Imported ${result.imported} review${result.imported === 1 ? "" : "s"}`, {
        description: result.skipped > 0 ? `${result.skipped} skipped (already stored or invalid).` : "They are picked up by the next run.",
      });
      setFile(null);
      setInputKey((k) => k + 1);
    } catch (err) {
      reportError(asApiError(err), "Import");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="import-title" className="space-y-4">
      <div>
        <h3 id="import-title" className="text-xl font-semibold">
          Import reviews (CSV)
        </h3>
        <p className="text-sm text-muted-foreground">
          For Google Play exports or any other source. Required columns: <span className="figure text-foreground">{CSV_REQUIRED.join(", ")}</span>.
        </p>
      </div>
      {token ? (
        <form onSubmit={upload} className="space-y-3">
          <label htmlFor={fileId} className="text-sm font-medium">
            CSV file
          </label>
          <input
            key={inputKey}
            id={fileId}
            type="file"
            accept=".csv,text/csv"
            aria-describedby={hintId}
            onChange={(e) => void pick(e.target.files?.[0] ?? null)}
            className="block w-full text-sm file:mr-3 file:rounded-md file:border file:border-border file:bg-card file:px-3 file:py-1.5 file:text-sm file:font-medium hover:file:bg-muted"
          />
          <p id={hintId} className={problem ? "text-xs text-rose-ink" : "text-xs text-muted-foreground"} role={problem ? "alert" : undefined}>
            {problem ?? `Columns read: ${CSV_COLUMNS.join(", ")}. Up to 2 MB. See Docs for the schema.`}
          </p>
          <Button type="submit" variant="outline" className="h-10" disabled={!file || busy}>
            {busy ? <Loader2Icon className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : <UploadIcon aria-hidden="true" />}
            Import
          </Button>
        </form>
      ) : (
        <ReadOnlyNotice label="import needs the token" />
      )}
    </section>
  );
}

function csvCell(v: string): string {
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

function ExportReplies({ appId }: { appId: string }) {
  const [busy, setBusy] = useState(false);

  // Mock mode has no API to serve the CSV, so build it here from the approved replies.
  const exportMock = async () => {
    setBusy(true);
    try {
      const approved = (await listProposals(appId, { status: "approved" })).filter((p) => p.kind === "reply");
      const rows = [["proposal_id", "review_id", "reply", "approved_at"], ...approved.map((p) => [p.id, p.review_id ?? "", p.draft.body ?? "", p.decided_at ?? ""])];
      const blob = new Blob([rows.map((r) => r.map(csvCell).join(",")).join("\n")], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `approved-replies-${appId}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      if (approved.length === 0) toast("No approved replies yet: the file has only the header row.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="export-title" className="space-y-4">
      <div>
        <h3 id="export-title" className="text-xl font-semibold">
          Export approved replies
        </h3>
        <p className="text-sm text-muted-foreground">Posting to the stores needs your developer credentials, so replies leave as a CSV you paste or upload yourself.</p>
      </div>
      {API_MOCK ? (
        <Button type="button" variant="outline" className="h-10" onClick={exportMock} disabled={busy}>
          <DownloadIcon aria-hidden="true" />
          Download CSV
        </Button>
      ) : (
        <a href={exportUrl(appId)} download className="inline-flex h-10 items-center gap-2 rounded-md border border-input bg-card px-4 text-sm font-medium hover:bg-muted">
          <DownloadIcon className="size-4" aria-hidden="true" />
          Download CSV
        </a>
      )}
    </section>
  );
}
