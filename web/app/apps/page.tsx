import type { Metadata } from "next";
import { AppsView } from "@/components/apps/apps-view";
import { container } from "@/lib/site";

export const metadata: Metadata = { title: "Apps" };

export default function AppsPage() {
  return (
    <div className={`${container} py-8 sm:py-12`}>
      <AppsView />
    </div>
  );
}
