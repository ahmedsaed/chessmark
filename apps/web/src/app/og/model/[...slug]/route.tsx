/**
 * A model's social card: its rating, its record, and a position it actually reached.
 *
 * **A Route Handler, not `models/[...slug]/opengraph-image.tsx`, and not by preference.** Next.js
 * refuses that file outright — *"catch all segment must be the last segment modifying the path"* —
 * because the image is served at a path *below* the catch-all, and a catch-all has already claimed
 * everything below it. A model's route has to stay a catch-all: a slug is `vendor/model:free`
 * today and nothing guarantees it stays two segments.
 *
 * So the card lives at `/og/model/<slug>` and the page names it in `generateMetadata`. The one
 * thing that buys us grief later: a colocated file is wired up automatically and this is not, so a
 * page that forgets to name it silently has no card again. `site.spec.ts` asserts every public
 * route has exactly one `og:image`, which is the only reason that is safe.
 *
 * **The board is one of this model's own ranked games, not the opening position.** Every other
 * card draws the start position as furniture, which is honest because it is plainly decoration.
 * Here it would not be: a card about one player, showing a board, is read as *that player's* game.
 * So either the board is one of theirs or there is no board — a model with nothing ranked gets a
 * wider panel instead, which is also the truer picture of a model that has not played.
 *
 * That costs one extra API read on a card revalidated every five minutes, which is the trade this
 * is worth making and the reason it is worth writing down.
 */

import { ImageResponse } from "next/og";

import { getGame, getModel } from "@/lib/api";
import { modelSlugFromSegments } from "@/lib/models";
import { Board } from "@/lib/og/board";
import { Card, CentredCard, Missing, Stats, Subtitle, Title, Wordmark } from "@/lib/og/shell";
import { CARD, COLOUR , REVALIDATE_SECONDS } from "@/lib/og/theme";
import type { LeaderboardRow } from "@/lib/types";

/* A Route Handler has no `alt`/`size`/`contentType` exports — those belong to the file convention.
   The alt text travels with the page's metadata instead; the size is `CARD`, below. */
/* **The literal, not `REVALIDATE_SECONDS`.** Next requires a segment config export to be
   statically analysable and fails the production build with "Invalid segment configuration export
   detected" if it is an imported constant — a `next build`-only error, which `make check` did not
   run and so did not catch. Five minutes; `og/theme.ts` holds the reasoning. */
export const revalidate = 300;

/**
 * No paths prerendered, but every path that *is* requested gets cached for `revalidate`.
 *
 * Without this Next marks the route dynamic — it cannot know the slugs — and a dynamic route
 * renders on every request however long its `revalidate` is. An empty list is the documented way
 * to say "generate on demand, then cache", which is what a social card wants: nobody waits on it,
 * and the slugs actually shared are a fraction of the slugs that exist.
 */
export async function generateStaticParams() {
  return [];
}

/**
 * The contestant to put on the card when a model is served at more than one precision.
 *
 * `model@fp8` and `model@fp4` are different entrants with different ratings (ADR-0015), and a card
 * has room for one number. The best-rated is the one a reader means by "how good is this model",
 * and the page behind the link shows every precision separately.
 */
function headline(ratings: LeaderboardRow[]): LeaderboardRow | null {
  return ratings.reduce<LeaderboardRow | null>(
    (best, row) => (best === null || row.rating > best.rating ? row : best),
    null,
  );
}

export async function GET(_request: Request, context: { params: Promise<{ slug: string[] }> }) {
  const { slug } = await context.params;
  const model = await getModel(modelSlugFromSegments(slug), { cache: REVALIDATE_SECONDS });

  if (!model) {
    return new ImageResponse(<Missing what="No such model" />, CARD);
  }

  const best = headline(model.ratings);
  const { stats } = model;

  /* The **most recent** ranked game of the best-rated contestant, so the board and the rating
     describe the same entrant and the position is the newest thing this model has done. First was
     stable and arbitrary; last is stable and current, and a card that goes stale as a model keeps
     playing is the wrong kind of stable. */
  const played = best ? model.rated_games?.[`${best.model_slug}@${best.quantization}`] : undefined;
  const gameId = played?.at(-1);
  const game = gameId ? await getGame(gameId, { cache: REVALIDATE_SECONDS }) : null;

  const figures = (
    <Stats
      items={[
        {
          value: best ? `${Math.round(best.rating)}${best.provisional ? "?" : ""}` : "unrated",
          label: "rating",
          tone: best ? COLOUR.accent : COLOUR.inkDim,
        },
        { value: `${stats.wins}/${stats.draws}/${stats.losses}`, label: "W/D/L" },
        {
          value: stats.illegal_per_move.toFixed(3),
          label: "illegal/move",
          // Zero is the good end of this scale — it is the benchmark's headline number, and a red
          // `0.000` would read as the opposite of what it says.
          tone: stats.illegal_per_move > 0 ? COLOUR.bad : COLOUR.good,
        },
      ]}
    />
  );

  // No ranked game means no board of its own, and nothing else belongs in that half of the card.
  if (!game) {
    return new ImageResponse(
      (
        <CentredCard>
          <Wordmark section="Model" />
          <Title size={58}>{model.display_name}</Title>
          <Subtitle>{model.openrouter_id}</Subtitle>
          {figures}
        </CentredCard>
      ),
      CARD,
    );
  }

  return new ImageResponse(
    (
      <Card board={<Board fen={game.current_fen} square={54} />}>
          <Wordmark section="Model" />
          <Title size={40}>{model.display_name}</Title>
          <Subtitle>{model.openrouter_id}</Subtitle>
          {figures}
      </Card>
    ),
    CARD,
  );
}
