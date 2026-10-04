import Link from "next/link";
import { container, ctaPrimary } from "@/lib/site";

export default function NotFound() {
  return (
    <div className={`${container} py-20`}>
      <p className="kicker">404 · no contact</p>
      <h1 className="mt-2 text-4xl font-bold">Nothing on the scope here.</h1>
      <p className="mt-3 max-w-md text-muted-foreground">The page you asked for does not exist, or the app or run was removed.</p>
      <Link href="/apps" className={`${ctaPrimary} mt-8`}>
        Back to apps
      </Link>
    </div>
  );
}
