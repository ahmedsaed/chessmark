/**
 * The ranking, on the front page: a podium for the top three and the chasing pack beside it.
 *
 * This was five rows in a list sharing a column with "Recent games", which said who was winning
 * without ever saying it was a *contest*. The podium is the whole pitch of the site in one glance
 * — these models are ranked against each other, and three of them are ahead.
 *
 * **The rating deviation travels with the rating everywhere it is shown.** A visitor comparing a
 * contestant with one game against one with four needs to see that difference in the same glance,
 * or the ordering reads as more settled than it is. Every place and every row carries it.
 *
 * A server component with no state: it is handed the stored ranking the lobby already awaited
 * (ADR-0032) and renders it. It asks for nothing of its own.
 */

import Link from "next/link";

import type { LeaderboardRow } from "@/lib/types";

/** Three on the podium, seven beside it — the top ten, which is what `/leaderboard` is for. */
const PODIUM = 3;
const CHASING = 7;

/**
 * Gold, silver, bronze — said in tokens, because no component hard-codes a colour (ADR-0013).
 *
 * First place takes the `accent`/`on-accent` pair the design system defines for exactly this: a
 * filled amber block with ink dark enough to read on it. Second and third step down through the
 * surfaces rather than inventing two more colours, so the podium reads as one object.
 *
 * The plinth heights are the podium. They are what makes three cards a ranking rather than three
 * cards, and they are the only thing distinguishing the places once the numbers are small — which
 * is why they shorten on a phone rather than disappearing: 56/36/24px still reads as a flight of
 * steps in a 390px row, and without them the three cards say nothing about who is ahead.
 */
const PLACES = [
  { card: "border-accent-dim", plinth: "h-14 sm:h-24 bg-accent text-on-accent", rating: "text-lg" },
  { card: "border-line", plinth: "h-9 sm:h-16 bg-surface-3 text-ink-dim", rating: "text-base" },
  { card: "border-line", plinth: "h-6 sm:h-10 bg-surface-2 text-ink-faint", rating: "text-base" },
] as const;

/**
 * `vendor/model` split apart.
 *
 * Production slugs run to 38 characters — `nvidia/nemotron-3-super-120b-a12b:free` — in a podium
 * column that is a sixth of the page, and 113px of it on a phone. Truncating that from the right
 * eats the *model*, which is the half a reader is looking for: at 1024px it printed
 * `nemotron-3-super-…` and the three cards became indistinguishable from each other. The vendor
 * goes on its own line and truncates, because it is the half that repeats and the half a reader
 * can infer; the model name wraps instead, to three lines on a phone if that is what it takes.
 */
function split(slug: string): { vendor: string; name: string } {
  const cut = slug.indexOf("/");
  return cut === -1
    ? { vendor: "", name: slug }
    : { vendor: slug.slice(0, cut), name: slug.slice(cut + 1) };
}

function href(row: LeaderboardRow): string {
  return `/models/${row.model_slug}#c-${encodeURIComponent(row.quantization)}`;
}

export function TopContestants({ rows, counted }: { rows: LeaderboardRow[]; counted: number }) {
  const podium = rows.slice(0, PODIUM);
  const chasing = rows.slice(PODIUM, PODIUM + CHASING);

  return (
    <section className="mt-16">
      <div className="mb-5 flex items-baseline gap-3">
        <h2 className="font-mono text-meta uppercase tracking-[0.18em] text-ink-faint">
          Leaderboard
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
        <Link
          href="/leaderboard"
          className="font-mono text-meta uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-accent"
        >
          All {rows.length} →
        </Link>
      </div>

      {rows.length === 0 ? (
        <p className="border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
          No ranked games yet. Ratings only move on games played in the fixed ranked
          configuration — unranked games are recorded but never counted.
        </p>
      ) : (
        <>
          {/* The podium is the wider half: it carries four numbers per contestant where the list
              carries one. At `lg` and below the two stack, podium first. */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.35fr_1fr] lg:gap-8">
            <Podium rows={podium} />
            {chasing.length > 0 && <Chasing rows={chasing} />}
          </div>

          <p className="tabular mt-3 font-mono text-meta text-ink-faint">
            Glicko-2 over {counted} ranked game{counted === 1 ? "" : "s"} · ± is the rating
            deviation, and a rating with a wide one has not settled
          </p>
        </>
      )}
    </section>
  );
}

function Podium({ rows }: { rows: LeaderboardRow[] }) {
  /* 2 · 1 · 3, the arrangement everyone already reads as a podium — but only once all three are
     there. With two contestants the reorder would print "2, 1" with no centre to make sense of it,
     so a short field stays in plain order.

     The DOM order is always 1, 2, 3: a screen reader and the tab key get the ranking, and only the
     painted order changes. That is the trade a visual podium costs, and it is the right way round
     — the reordering is decoration, the sequence is the information. */
  const staged = rows.length === PODIUM;

  return (
    /* **Three columns at every width, including a phone.** The alternative was stacking them into
       a plain ranked list below `sm`, which is legible and says nothing: a list is what this
       section replaced. A 113px column holds a vendor, a wrapped name, a rating and a W/D/L, and
       the two figures that do not fit — the games count and the illegal-move rate — wait for `sm`
       rather than shredding the three cards to keep them. Measured at 390px and at 360px: no
       horizontal overflow at either, which the phone suite asserts. */
    <ol aria-label="The top three" className="grid grid-cols-3 items-end gap-1.5 sm:gap-3">
      {rows.map((row, index) => {
        const place = PLACES[index];
        const { vendor, name } = split(row.model_slug);
        const order = staged ? ["order-2", "order-1", "order-3"][index] : "";

        return (
          <li key={`${row.model_slug}@${row.quantization}`} className={`flex flex-col ${order}`}>
            <Link
              href={href(row)}
              /* **`prefetch={false}` is not a preference; it stops a request storm.** A visible
                 link to `/models/[...slug]` prefetches, the payload comes back `no-store` because
                 every route here is dynamic (the root layout reads the request), so the router
                 stores nothing and schedules the prefetch again — ~110 requests a second per link
                 on a production build, ~90 in twelve seconds on the live site. Not Clerk: it
                 reproduces with Clerk disabled. Not this page: `/leaderboard` and
                 `/tournaments/{slug}` do it today. Prefetching an uncacheable route buys nothing,
                 so this costs a fetch on click and removes the loop (FRONTEND.md). */
              prefetch={false}
              /* **One height for all three, fixed rather than a minimum.** The plinth is the only
                 thing that may stagger the tops, and with `min-h` it was not: on a phone, second
                 place's name wrapped to three lines and its precision badge took a fourth, which
                 made that card 29px taller than first place's against a 20px plinth difference —
                 so the card above the *shorter* plinth stood higher and the staircase read
                 backwards. The name below clamps for the same reason. */
              className={`flex h-[10.5rem] flex-col justify-between gap-1.5 overflow-hidden border bg-surface-2 p-2 transition-colors hover:border-accent focus-visible:border-accent sm:gap-2 sm:p-3 ${place.card}`}
            >
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="min-w-0 max-w-full truncate font-mono text-meta text-ink-faint">
                  {vendor}
                </span>
                <span className="flex-none border border-good/40 px-1 py-px font-mono text-label uppercase tracking-wider text-good">
                  {row.quantization}
                </span>
              </div>

              {/* Three lines is what a 113px column holds, and every production name fits it —
                  `nemotron-3-super-120b-a12b:free` is the longest at 31 characters. The clamp is a
                  safety valve for a longer one: it would otherwise push the card past the height
                  above and flip the podium. The phone suite fails if a real name ever needs it. */}
              <p
                className="line-clamp-3 flex-none break-words font-mono text-xs text-ink"
                title={row.model_slug}
              >
                {name}
              </p>

              <p className="tabular flex items-baseline gap-1.5 font-mono">
                <span data-testid="rating" className={`text-accent ${place.rating}`}>
                  {Math.round(row.rating)}
                </span>
                <span className="text-meta text-ink-faint">
                  ± {Math.round(row.rating_deviation)}
                </span>
              </p>

              <p className="tabular flex flex-wrap items-baseline gap-x-2 font-mono text-meta text-ink-faint">
                <span className="text-ink-dim" title="wins / draws / losses">
                  {row.wins}/{row.draws}/{row.losses}
                </span>
                <span className="hidden sm:inline">
                  {row.games} game{row.games === 1 ? "" : "s"}
                </span>
                {/* The benchmark's headline number, on the face of the card. It is the reason the
                    project exists and it is the one figure a rating cannot tell you. */}
                <span
                  className={`hidden sm:inline ${row.illegal_per_move > 0 ? "text-bad" : "text-good"}`}
                  title={`${row.illegal_attempts} illegal attempts over ${row.moves_played} moves`}
                >
                  {row.illegal_per_move.toFixed(3)} ill/move
                </span>
              </p>
            </Link>

            <div
              aria-hidden
              className={`flex items-center justify-center border-x border-b border-line font-serif text-xl leading-none sm:text-2xl ${place.plinth}`}
            >
              {index + 1}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** Places four onward: one line each, the rating and its deviation, nothing else. */
function Chasing({ rows }: { rows: LeaderboardRow[] }) {
  return (
    <ol
      aria-label="Places four onward"
      className="flex flex-col gap-px self-start border border-line-soft bg-line-soft"
    >
      {rows.map((row, index) => (
        <li key={`${row.model_slug}@${row.quantization}`}>
          <Link
            href={href(row)}
            /* The same request storm as the podium above. */
            prefetch={false}
            className="flex items-center gap-3 bg-surface px-4 py-2.5 transition-colors hover:bg-surface-2"
          >
            <span className="tabular w-4 flex-none font-mono text-data text-ink-faint">
              {index + PODIUM + 1}
            </span>
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink">
              {row.model_slug}
              <span className="text-ink-faint">@{row.quantization}</span>
            </span>
            <span data-testid="rating" className="tabular flex-none font-mono text-xs text-accent">
              {Math.round(row.rating)}
              <span className="ml-1 text-meta text-ink-faint">
                ± {Math.round(row.rating_deviation)}
              </span>
            </span>
          </Link>
        </li>
      ))}
    </ol>
  );
}
