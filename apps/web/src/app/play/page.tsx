import type { Metadata } from "next";

import { MyGames } from "@/components/MyGames";
import { NewGameSection } from "@/components/NewGameSection";
import { apiUrl, listModels } from "@/lib/api";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Play",
  description: "Play a language model yourself, or start a game between two of them.",
};

/**
 * Starting a game, and returning to one.
 *
 * The form used to sit in the middle of the landing page, between the results and the model list,
 * which made the front page part shop window and part control panel. It has its own route now.
 *
 * Your own games live here rather than on a route of their own: "where do I have a move to make"
 * and "start another one" are the same visit. Before this, a human game was reachable only by
 * keeping hold of its URL.
 *
 * This is still a holding page for the picker as it stands — model *selection* deserves the search
 * and the model pages that Phase 20 covers.
 */
export default async function PlayPage() {
  const models = await listModels();

  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12">
      <h1 className="font-serif text-4xl leading-tight text-ink">Play</h1>
      <p className="mt-4 max-w-prose text-ink-dim">
        Sit down against a model, or put two of them against each other and watch. Either way the
        game is unranked unless it runs the fixed ranked configuration, and it stops on its own at
        the ply cap or the spend cap, whichever comes first.
      </p>

      <div className="mt-8">
        <NewGameSection apiUrl={apiUrl} models={models} />
      </div>

      <MyGames />
    </main>
  );
}
