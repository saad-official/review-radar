import type { Metadata } from "next";
import { AppView } from "@/components/app/app-view";
import { container } from "@/lib/site";

export async function generateMetadata({ params }: PageProps<"/apps/[id]">): Promise<Metadata> {
  const { id } = await params;
  return { title: `App ${id}`, robots: { index: false } };
}

export default async function AppPage({ params }: PageProps<"/apps/[id]">) {
  const { id } = await params;
  return (
    <div className={`${container} py-8 sm:py-10`}>
      {/* Keyed by id so moving between apps starts from a clean state. */}
      <AppView key={id} id={id} />
    </div>
  );
}
