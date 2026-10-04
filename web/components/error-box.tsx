import { TriangleAlertIcon } from "lucide-react";
import { errorTitle, type ApiError } from "@/lib/errors";
import { cn } from "@/lib/utils";

export function ErrorBox({ error, title, className, action }: { error: ApiError; title?: string; className?: string; action?: React.ReactNode }) {
  return (
    <div role="alert" className={cn("flex gap-3 rounded-md border border-rose/35 bg-rose-wash p-4 text-rose-ink", className)}>
      <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0 space-y-1">
        <p className="font-semibold">{title ?? errorTitle(error)}</p>
        <p className="text-sm break-words text-foreground/85">{error.message}</p>
        {action ? <div className="pt-2">{action}</div> : null}
      </div>
    </div>
  );
}

export function EmptyState({ title, children, className }: { title: string; children?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-md border border-dashed border-border px-5 py-10 text-center", className)}>
      <p className="font-heading text-lg font-semibold">{title}</p>
      {children ? <div className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">{children}</div> : null}
    </div>
  );
}
