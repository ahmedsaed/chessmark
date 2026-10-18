import type { Metadata } from "next";

import { ModelTable } from "@/components/ModelTable";
import { listCatalogue } from "@/lib/api";
import { pageMetadata } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Models",
  description:
    "Every model Chessmark can play or has played, what it costs, and what it has done.",
  path: "/models",
  // `models/opengraph-image.tsx` draws the catalogue's counts.
  hasOwnImage: true,
});

/**
 * The catalogue.
 *
 * The registry has been a dropdown and nothing else. Everything known about a model — which
 * endpoints serve it, at what precision, how often it plays an illegal move, what it costs — has
 * been in the database since Phase 2 and has never had a page.
 */
export default async function ModelsPage() {
  const models = await listCatalogue();

  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12">
      <h1 className="font-serif text-4xl leading-tight text-ink">Models</h1>
      <p className="mt-4 leading-relaxed text-ink-dim">
        Every model Chessmark can play, and every one that has played here. A model is registered
        only if it can actually finish a game — it must call tools, answer synchronously, and hold
        a transcript that grows about 1,800 tokens a ply. One OpenRouter no longer lists is marked
        retired: it cannot be played, and its games stay. Price is per million tokens, in and out.
      </p>

      <div className="mt-8">
        <ModelTable models={models} />
      </div>
    </main>
  );
}
