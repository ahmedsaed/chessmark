import type { Metadata } from "next";

import { NewGameSection } from "@/components/NewGameSection";
import { apiUrl, listModels } from "@/lib/api";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "New game",
  description: "Start a game between two language models.",
};

/**
 * Starting a game.
 *
 * The form used to sit in the middle of the landing page, between the results and the model list,
 * which made the front page part shop window and part control panel. It has its own route now.
 *
 * This is a holding page for the form as it stands — model *selection* deserves the search and the
 * model pages that Phase 19 covers.
 */
export default async function PlayPage() {
  const models = await listModels();

  return (
    <main className="mx-auto w-full max-w-[760px] flex-1 px-5 py-12">
      <h1 className="font-serif text-4xl leading-tight text-ink">New game</h1>
      <p className="mt-4 max-w-prose text-ink-dim">
        Pick two models and watch them play. The game is unranked unless it runs the fixed ranked
        configuration, and it stops on its own at the ply cap or the spend cap, whichever comes
        first.
      </p>

      <div className="mt-8">
        <NewGameSection apiUrl={apiUrl} models={models} />
      </div>
    </main>
  );
}
