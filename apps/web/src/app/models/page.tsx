import type { Metadata } from "next";

import { ModelTable } from "@/components/ModelTable";
import { listModels } from "@/lib/api";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Models",
  description: "Every model Chessmark can play, what it costs, and what it has done.",
};

/**
 * The catalogue.
 *
 * The registry has been a dropdown and nothing else. Everything known about a model — which
 * endpoints serve it, at what precision, how often it plays an illegal move, what it costs — has
 * been in the database since Phase 2 and has never had a page.
 */
export default async function ModelsPage() {
  const models = await listModels();

  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12">
      <h1 className="font-serif text-4xl leading-tight text-ink">Models</h1>
      <p className="mt-4 max-w-prose text-ink-dim">
        Every model Chessmark can play. A model is registered only if it can actually finish a
        game — it must call tools, answer synchronously, and hold a transcript that grows about
        1,800 tokens a ply. Price is per million tokens, in and out.
      </p>

      <div className="mt-8">
        <ModelTable models={models} />
      </div>
    </main>
  );
}
