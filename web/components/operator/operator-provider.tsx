"use client";

import { createContext, useCallback, useContext, useId, useMemo, useState } from "react";
import { KeyRoundIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useOperatorToken } from "@/hooks/use-operator-token";
import { API_MOCK } from "@/lib/config";
import type { ApiError } from "@/lib/errors";
import { errorTitle } from "@/lib/errors";
import { maskToken, writeOperatorToken } from "@/lib/operator";
import { cn } from "@/lib/utils";

type OperatorContext = {
  /** Open the operator settings drawer. */
  openSettings: () => void;
  /** Show an error toast; 401s get a button that opens the drawer. */
  reportError: (err: ApiError, what?: string) => void;
};

const Ctx = createContext<OperatorContext | null>(null);

export function useOperator(): OperatorContext {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useOperator must be used inside <OperatorProvider>");
  return ctx;
}

export function OperatorProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const openSettings = useCallback(() => setOpen(true), []);
  const reportError = useCallback(
    (err: ApiError, what?: string) => {
      const title = what ? `${what}: ${errorTitle(err).toLowerCase()}` : errorTitle(err);
      toast.error(title, {
        description: err.retryAfter ? `${err.message} Retry in ${Math.ceil(err.retryAfter)} s.` : err.message,
        action: err.code === "unauthorized" ? { label: "Add token", onClick: () => setOpen(true) } : undefined,
      });
    },
    [],
  );
  const value = useMemo(() => ({ openSettings, reportError }), [openSettings, reportError]);
  return (
    <Ctx.Provider value={value}>
      {children}
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="w-full gap-0 sm:max-w-md">
          {/* Keyed on open so the form starts empty each time. */}
          {open ? <OperatorForm onDone={() => setOpen(false)} /> : null}
        </SheetContent>
      </Sheet>
    </Ctx.Provider>
  );
}

function OperatorForm({ onDone }: { onDone: () => void }) {
  const token = useOperatorToken();
  const [value, setValue] = useState("");
  const inputId = useId();
  const hintId = useId();

  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (value.trim() === "") return;
    writeOperatorToken(value);
    toast.success("Operator token saved in this browser");
    onDone();
  };

  const clear = () => {
    writeOperatorToken(null);
    toast("Operator token removed. This browser is read-only now.");
  };

  return (
    <>
      <SheetHeader className="border-b border-border p-5 pr-12">
        <p className="kicker">Operator</p>
        <SheetTitle className="text-xl font-semibold">Operator token</SheetTitle>
        <SheetDescription>
          Review Radar has no accounts. One shared token (the API&apos;s <code className="figure">OPERATOR_TOKEN</code>) unlocks
          every write: adding apps, starting runs, approving and rejecting proposals, importing reviews.
        </SheetDescription>
      </SheetHeader>
      <div className="space-y-6 overflow-y-auto p-5">
        <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-muted/50 px-3 py-2.5">
          <span className="text-sm">This browser</span>
          {token ? (
            <span className="figure inline-flex items-center gap-2 text-sm text-radar-ink">
              <span aria-hidden="true" className="size-1.5 rounded-full bg-radar" />
              operator · {maskToken(token)}
            </span>
          ) : (
            <span className="figure inline-flex items-center gap-2 text-sm text-muted-foreground">
              <span aria-hidden="true" className="size-1.5 rounded-full bg-slate/60" />
              read-only
            </span>
          )}
        </div>

        <form onSubmit={save} className="space-y-3">
          <label htmlFor={inputId} className="block text-sm font-medium">
            {token ? "Replace token" : "Token"}
          </label>
          <Input
            id={inputId}
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-describedby={hintId}
            className="figure h-10"
            placeholder="paste the operator token"
          />
          <p id={hintId} className="text-xs leading-relaxed text-muted-foreground">
            Stored in this browser&apos;s localStorage only and sent as <code className="figure">Authorization: Bearer …</code>{" "}
            on write requests. Reading apps, themes, reviews and runs needs no token.
            {API_MOCK ? " Mock mode: any value works." : null}
          </p>
          <Button type="submit" size="lg" className="h-10 w-full" disabled={value.trim() === ""}>
            Save token
          </Button>
        </form>
      </div>
      <SheetFooter className="border-t border-border p-5">
        <Button type="button" variant="outline" className="h-10" onClick={clear} disabled={!token}>
          Forget token on this browser
        </Button>
      </SheetFooter>
    </>
  );
}

/** Header control: shows whether this browser can write, and opens the drawer. */
export function OperatorButton({ className }: { className?: string }) {
  const token = useOperatorToken();
  const { openSettings } = useOperator();
  return (
    <button
      type="button"
      onClick={openSettings}
      className={cn(
        "figure inline-flex h-9 items-center gap-2 rounded-md border border-border px-2.5 text-xs font-medium text-foreground/80 hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground",
        className,
      )}
      aria-label={token ? "Operator settings (token set)" : "Operator settings (read-only, no token)"}
    >
      <KeyRoundIcon className="size-3.5" aria-hidden="true" />
      <span aria-hidden="true" className={cn("size-1.5 rounded-full", token ? "bg-radar" : "bg-slate/60")} />
      <span className="hidden sm:inline" aria-hidden="true">
        {token ? "operator" : "read-only"}
      </span>
    </button>
  );
}

/** Inline marker where a write would be: "read-only · add token". */
export function ReadOnlyNotice({ className, label = "read-only" }: { className?: string; label?: string }) {
  const { openSettings } = useOperator();
  return (
    <button
      type="button"
      onClick={openSettings}
      className={cn(
        "figure inline-flex h-7 items-center gap-1.5 rounded-sm border border-dashed border-border px-2 text-xs text-muted-foreground hover:border-foreground/40 hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground",
        className,
      )}
    >
      <KeyRoundIcon className="size-3" aria-hidden="true" />
      {label}
      <span className="text-foreground/60">· add token</span>
    </button>
  );
}
