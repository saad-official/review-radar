"use client";

import { useId, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2Icon, PlusIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useOperator } from "@/components/operator/operator-provider";
import { createApp, type Store } from "@/lib/api";
import { asApiError } from "@/lib/errors";
import { parseAppStoreId } from "@/lib/format";

const COUNTRIES = [
  ["us", "United States"],
  ["gb", "United Kingdom"],
  ["ca", "Canada"],
  ["au", "Australia"],
  ["de", "Germany"],
  ["fr", "France"],
  ["in", "India"],
  ["jp", "Japan"],
  ["br", "Brazil"],
  ["ae", "United Arab Emirates"],
] as const;

const REPO_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\/[A-Za-z0-9._-]{1,100}$/;

export function AddAppDialog() {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button type="button" className="h-10 px-4">
          <PlusIcon aria-hidden="true" />
          Add app
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-lg">{open ? <AddAppForm onDone={() => setOpen(false)} /> : null}</DialogContent>
    </Dialog>
  );
}

function AddAppForm({ onDone }: { onDone: () => void }) {
  const router = useRouter();
  const { reportError } = useOperator();
  const store: Store = "ios";
  const [storeId, setStoreId] = useState("");
  const [country, setCountry] = useState("us");
  const [repo, setRepo] = useState("");
  const [policy, setPolicy] = useState("");
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const storeFieldId = useId();
  const storeIdId = useId();
  const storeIdHint = useId();
  const countryId = useId();
  const repoId = useId();
  const repoHint = useId();
  const policyId = useId();

  const parsedId = parseAppStoreId(storeId);
  const idError = serverErrors.store_id || (touched && !parsedId ? "Enter the numeric App Store id, or paste the app's App Store link." : undefined);
  const repoError = serverErrors.github_repo || (repo.trim() !== "" && !REPO_RE.test(repo.trim()) ? "Use owner/name." : undefined);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!parsedId || repoError || busy) return;
    setBusy(true);
    setServerErrors({});
    try {
      const app = await createApp({ store, store_id: parsedId, country, github_repo: repo, policy });
      toast.success(`${app.name} added`, { description: "Run it now to fetch its first reviews." });
      onDone();
      router.push(`/apps/${encodeURIComponent(app.id)}`);
    } catch (err) {
      const apiErr = asApiError(err);
      if (apiErr.code === "invalid_input") setServerErrors(apiErr.fields);
      reportError(apiErr, "Add app");
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} noValidate className="grid gap-5">
      <DialogHeader>
        <DialogTitle className="text-xl">Add an app</DialogTitle>
        <DialogDescription>Public App Store reviews (about 500 recent reviews per country). Google Play reviews come in through CSV import.</DialogDescription>
      </DialogHeader>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          <label htmlFor={storeFieldId} className="text-sm font-medium">
            Store
          </label>
          <Input id={storeFieldId} value="App Store (iOS)" readOnly className="h-10" />
        </div>
        <div className="grid gap-2">
          <label htmlFor={countryId} className="text-sm font-medium">
            Country
          </label>
          <Select value={country} onValueChange={setCountry}>
            <SelectTrigger id={countryId} className="h-10 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {COUNTRIES.map(([code, name]) => (
                <SelectItem key={code} value={code}>
                  {name} ({code})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="grid gap-2">
        <label htmlFor={storeIdId} className="text-sm font-medium">
          App Store id
        </label>
        <Input
          id={storeIdId}
          value={storeId}
          onChange={(e) => {
            setStoreId(e.target.value);
            setServerErrors((s) => ({ ...s, store_id: "" }));
          }}
          onBlur={() => setTouched(true)}
          autoComplete="off"
          placeholder="6450012345 or the App Store link"
          className="figure h-10"
          aria-invalid={idError ? true : undefined}
          aria-describedby={storeIdHint}
          required
        />
        <p id={storeIdHint} className={idError ? "text-xs text-rose-ink" : "text-xs text-muted-foreground"}>
          {idError || (parsedId && parsedId !== storeId.trim() ? `Using id ${parsedId}.` : "The number after /id in the app's App Store link.")}
        </p>
      </div>

      <div className="grid gap-2">
        <label htmlFor={repoId} className="text-sm font-medium">
          GitHub repo for issues <span className="font-normal text-muted-foreground">(optional)</span>
        </label>
        <Input
          id={repoId}
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          placeholder="owner/name"
          className="figure h-10"
          spellCheck={false}
          autoCapitalize="off"
          aria-invalid={repoError ? true : undefined}
          aria-describedby={repoHint}
        />
        <p id={repoHint} className={repoError ? "text-xs text-rose-ink" : "text-xs text-muted-foreground"}>
          {repoError || "Approved issues are created here. Without it, issues can still be approved and copied."}
        </p>
      </div>

      <div className="grid gap-2">
        <label htmlFor={policyId} className="text-sm font-medium">
          Reply policy <span className="font-normal text-muted-foreground">(optional)</span>
        </label>
        <Textarea id={policyId} value={policy} onChange={(e) => setPolicy(e.target.value)} rows={4} placeholder="Tone, what never to promise, when to propose an issue" />
      </div>

      <DialogFooter className="gap-2">
        <Button type="button" variant="outline" className="h-10" onClick={onDone} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" className="h-10" disabled={busy}>
          {busy ? <Loader2Icon className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : null}
          Add app
        </Button>
      </DialogFooter>
    </form>
  );
}
