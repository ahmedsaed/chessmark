/**
 * The left rail: who is playing, how they are doing, and what it costs.
 *
 * `illegal` sits on the face of the card rather than in a methodology page. It is the benchmark's
 * most interesting number and the whole reason the project exists.
 *
 * The move list used to live here and now sits in the conversation as a filter — a move is an
 * event in the same timeline as everything else, and keeping a second copy beside it meant two
 * places to look and two things to keep in sync.
 *
 * **A player's token count is prompt + completion**, the same sum the game's own total is built
 * from (`Game.total_tokens`). It read `prompt_tokens` alone, which was wrong in a way only a human
 * game made obvious: a person burns no tokens, so the one model's card should have matched the
 * game total exactly, and it was short by precisely the completion count.
 */

import Link from "next/link";

import type { GameDetail, GameTournament, Player, SeatStanding } from "@/lib/types";

function usd(value: string): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  if (amount === 0) return "$0.000";
  return amount < 0.001 ? `$${amount.toFixed(6)}` : `$${amount.toFixed(3)}`;
}

/**
 * Cached share of the **prompt**, not of every token.
 *
 * Deliberately a different denominator from the Tokens stat beside it: only prompt tokens can be
 * cached, so dividing by the total would quietly report a lower rate than the provider achieved
 * and make NFR-06's ">80%" unreachable by arithmetic.
 */
function cacheRate(player: Player): string {
  if (!player.prompt_tokens) return "—";
  return `${Math.round((player.cached_tokens / player.prompt_tokens) * 100)}%`;
}

export function StatsRail({
  game,
  toMove,
  activePly,
  event,
}: {
  game: GameDetail;
  toMove: "white" | "black" | null;
  /** Replay only: how far through the game the board currently is. */
  activePly?: number;
  /** The event that scheduled this game, when one did. Fetched apart from the game. */
  event?: GameTournament | null;
}) {
  const white = game.players.find((p) => p.colour === "white");
  const black = game.players.find((p) => p.colour === "black");
  const hasHuman = game.players.some((p) => p.kind === "human");

  return (
    <aside aria-label="Game statistics" className="flex min-w-0 flex-col gap-2.5">
      {/* **Two rows, four facts.** Each used to have a line of its own, which cost 110px of a rail
          that has 616 to spend at 1366x768 — and the four are two pairs anyway: where the game is,
          and what kind of game it is. Nothing was dropped to fit; they were paired. */}
      <div className="flex flex-col gap-1.5 border border-line bg-surface-2 p-2.5">
        <Row
          label="Move"
          value={`${Math.ceil((activePly ?? game.ply_count) / 2) || 1} · ${
            activePly === undefined ? game.ply_count : `${activePly}/${game.ply_count}`
          } plies`}
        />
        {/* A game with a person in it can never be ranked — a person is not a contestant — so the
            row would read "no" for the whole game and tell the player nothing they did not choose.
            Whether chat is on is still worth stating, because they chose that too. */}
        <Row
          label="Status"
          value={`${game.status} · ${
            hasHuman
              ? `talk ${game.trash_talk_enabled ? "on" : "off"}`
              : game.is_ranked
                ? "ranked"
                : `unranked · talk ${game.trash_talk_enabled ? "on" : "off"}`
          }`}
          muted
        />
      </div>

      {white && (
        <PlayerCard
          player={white}
          active={toMove === "white"}
          won={game.winner_colour === "white"}
        />
      )}
      {black && (
        <PlayerCard
          player={black}
          active={toMove === "black"}
          won={game.winner_colour === "black"}
        />
      )}

      {/* The heading was the tallest thing in here and said the least — "Spend" over a row reading
          "Total $0.000" is the same word twice. Three facts, one line, no heading. */}
      <dl className="grid grid-cols-3 gap-2 border border-line bg-surface-2 p-2.5">
        <Cell label="Spend" value={usd(game.total_cost_usd)} />
        <Cell label="Cap" value={game.max_usd ? usd(game.max_usd) : "none"} />
        <Cell label="Tokens" value={game.total_tokens.toLocaleString()} />
      </dl>

      {event && <EventCard event={event} />}
    </aside>
  );
}

/**
 * Where this game came from, and how its two players stand in it.
 *
 * Shown only when the game came from somewhere. Most games are started by hand, so the card is
 * absent rather than empty — a row reading "Event —" on every casual game would be a block of
 * nothing on the common case.
 *
 * **A two-row slice of the standings table**, rather than a breadcrumb. The event's name and round
 * a reader could have guessed from the tournament page; what they cannot get anywhere else is how
 * these two models — the ones actually on the board above — compare inside the event that put them
 * there. That is the question the card exists to answer, so the seats are the card and the name is
 * the heading.
 *
 * **The era is the other row that earns its place.** It says which results this one is comparable
 * to: a game played under `v2+v3` answered a different prompt with different tools and is not the
 * same measurement (ADR-0043). The table below it is scoped to that era for the same reason.
 *
 * **The standings still move, and the footnote says so.** A game early in an era sits beside a
 * table that later games have changed. Storing a snapshot per game would fix that and is not worth
 * a column; saying which table this is costs a line.
 */
function EventCard({ event }: { event: GameTournament }) {
  const { tournament, seats, ranked_by, entrants } = event;
  const byRating = ranked_by === "rating";

  return (
    <div className="flex flex-col gap-1.5 border border-line bg-surface-2 p-2.5">
      <div className="flex min-w-0 items-baseline gap-2">
        <Link
          href={`/tournaments/${tournament.slug}`}
          className="truncate font-mono text-[11px] text-accent hover:underline"
        >
          {tournament.name}
        </Link>
        <span
          className="ml-auto flex-none cursor-help font-mono text-[8.5px] text-ink-faint"
          title={[
            tournament.format === "pool"
              ? `Round ${tournament.round_number}: a pool numbers one round per game, so this is the Nth pairing it scheduled.`
              : `Round ${tournament.round_number} of ${entrants} entrants.`,
            tournament.era
              ? `Era ${tournament.era} is the prompt and tool-schema majors this game was played under — results are comparable within an era, not across one.`
              : "",
            `${byRating ? "Rating" : "Score"} and place are this era's table as it stands now, not as it stood when this game was played.`,
          ]
            .filter(Boolean)
            .join(" ")}
        >
          r{tournament.round_number}
          {tournament.era ? ` · ${tournament.era}` : ""}
        </span>
      </div>

      {/* **The columns are headed.** They were not, and a line ending "#1/8  1" leaves the reader
          to guess what the last number is — the one thing the event ranks on. Two words of 8px
          type buy that back for twelve pixels. */}
      <dl className="grid grid-cols-[auto_1fr_auto_auto] items-baseline gap-x-2.5 gap-y-0.5 font-mono text-[10px]">
        <dt className="col-span-2" />
        <dt className="text-right text-[7.5px] uppercase tracking-[0.1em] text-ink-faint">place</dt>
        <dt className="text-right text-[7.5px] uppercase tracking-[0.1em] text-ink-faint">
          {byRating ? "rating" : "score"}
        </dt>
        {seats.map((seat) => (
          <SeatLine key={seat.colour} seat={seat} byRating={byRating} entrants={entrants} />
        ))}
      </dl>
    </div>
  );
}

/**
 * One seat's line: swatch, name, place, and the number the event is ranked on.
 *
 * **One line, not a card.** It was a name row over a three-column stat grid, which read beautifully
 * and cost 271px — in a rail that has 616 at 1366x768 and four other blocks to fit. The facts are
 * all still here; they are on one line instead of three.
 *
 * The record and the "as they stand now" caveat moved into `title` attributes. Both are things a
 * reader wants *once*, when they first wonder what the numbers mean — not on every game page for
 * the rest of the rail's life.
 *
 * A seat with no `place` is one the table does not carry: a human, or a model seated outside the
 * field. It keeps its line and says so, because an absent row would read as a rendering fault.
 */
function SeatLine({
  seat,
  byRating,
  entrants,
}: {
  seat: SeatStanding;
  byRating: boolean;
  entrants: number;
}) {
  const rank =
    byRating && seat.rating !== null
      ? `${Math.round(seat.rating)}${seat.rating_provisional ? "?" : ""}`
      : byRating
        ? "unrated"
        : String(seat.score);

  return (
    <>
      <dt className="flex min-w-0 items-center gap-1.5">
        <i
          aria-hidden
          className={`block h-2 w-2 flex-none border border-line ${
            seat.colour === "white" ? "bg-piece-white" : "bg-piece-black"
          }`}
        />
      </dt>
      <dd className="min-w-0 truncate text-ink-dim" title={seat.display_name}>
        {seat.display_name}
        {!seat.in_field && (
          <span
            className="ml-1 cursor-help text-ink-faint"
            title="no longer in this event's field — it keeps its record but gets no new pairings"
          >
            · left
          </span>
        )}
      </dd>
      {seat.place === null ? (
        <dd className="col-span-2 text-right text-ink-faint">not entered</dd>
      ) : (
        <>
          <dd
            className="tabular text-right text-ink-faint"
            title={`${seat.wins}W ${seat.draws}D ${seat.losses}L over ${seat.played} games in this era`}
          >
            #{seat.place}/{entrants}
          </dd>
          <dd
            className="tabular text-right text-ink"
            title={
              byRating && seat.rating_provisional
                ? "still too few games for this rating to be read as a placing"
                : byRating
                  ? "Glicko-2 over this event's games in this era"
                  : "points in this era"
            }
          >
            {rank}
          </dd>
        </>
      )}
    </>
  );
}

function Cell({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div title={title} className={title ? "cursor-help" : undefined}>
      <dt className="font-mono text-[8.5px] uppercase tracking-[0.1em] text-ink-faint">{label}</dt>
      <dd className="tabular mt-0.5 font-mono text-[11px] text-ink">{value}</dd>
    </div>
  );
}

function PlayerCard({
  player,
  active,
  won,
}: {
  player: Player;
  active: boolean;
  won: boolean;
}) {
  return (
    <div
      /* `relative` and `overflow-hidden` for the ribbon below, which is positioned against this
         card and clipped by its corner. */
      className={`relative flex flex-col gap-1.5 overflow-hidden border p-2.5 ${
        won
          ? "border-accent bg-surface-3"
          : active
            ? "border-accent-deep bg-surface-3"
            : "border-line bg-surface-2"
      }`}
    >
      {/* **A band across the corner, from a finished game only.** `winner_colour` is null until
          the game ends and null for every draw, so this appears exactly when there is a winner —
          it never needs to ask whether the game is over. The card already carries the result in
          its border; the ribbon is for someone scanning the page rather than reading it.

          `aria-hidden` with the text repeated for a screen reader below it: a rotated band reads
          as nonsense out of order, and "winner" belongs next to the model's name in the reading
          order rather than diagonally across it.

          **Moving it keeps one rule: `|right| ≈ top + half the band's height`.** The word is
          centred in a `w-28` box, the corner clips one end of that box, and the text only looks
          centred while the box sits symmetrically across the corner's diagonal. Nudging `top`
          alone slides the word along the band and off centre — which is what `-right-8 top-3`
          did, by four pixels. Change both together: smaller numbers tuck it into the corner,
          larger ones push it down into the card. */}
      {won && (
        <>
          <span className="sr-only">Winner.</span>
          <span
            aria-hidden
            className="pointer-events-none absolute -right-8 top-4 w-28 rotate-45 bg-accent py-0.5 text-center font-mono text-[8.5px] uppercase tracking-[0.14em] text-on-accent"
          >
            winner
          </span>
        </>
      )}
      <div className="flex min-w-0 items-center gap-2">
        <i
          aria-hidden
          className={`block h-2.5 w-2.5 flex-none border border-line ${
            player.colour === "white" ? "bg-piece-white" : "bg-piece-black"
          }`}
        />
        <b className="truncate font-mono text-xs font-normal text-ink">
          {player.display_name}
        </b>
        {active && (
          <span className="ml-auto flex-none border border-accent-deep px-1.5 py-px font-mono text-[8.5px] uppercase tracking-[0.12em] text-accent">
            to move
          </span>
        )}
      </div>
      <p className="truncate font-mono text-[9px] leading-tight text-ink-faint">
        {player.model ?? player.kind}
      </p>

      <Endpoint player={player} />

      {/* **One row, not two.** Four stats in a 2x2 grid cost twice the height for the same four
          numbers, and the rail has 616px at 1366x768 to fit two of these cards, the status, the
          spend and the event.
          The column count follows the stat count for the same reason: `Compacted` appears only
          when it has happened, and a fifth stat in a four-column grid wraps — turning the one case
          where a seat has *more* to say into the one that costs another row on both cards. */}
      <dl
        className={`grid gap-px border border-line-soft bg-line-soft ${
          player.compactions > 0 ? "grid-cols-5" : "grid-cols-4"
        }`}
      >
        <Stat label="Tokens" value={(player.prompt_tokens + player.completion_tokens).toLocaleString()} />
        <Stat label="Cached" value={cacheRate(player)} />
        <Stat label="Cost" value={usd(player.total_cost_usd)} />
        <Stat
          label="Illegal"
          value={String(player.illegal_attempts)}
          tone={player.illegal_attempts > 0 ? "bad" : "good"}
        />
        {/* Only once it has happened. A "Compacted 0" on every game would be a row of noise on the
            common case, and the number says something about the model when it is not zero: a seat
            that compacted four times filled its window four times in one game. */}
        {player.compactions > 0 && (
          <Stat label="Compacted" value={String(player.compactions)} />
        )}
      </dl>
    </div>
  );
}

/**
 * Which endpoint served this seat, and whether the pin held.
 *
 * Both halves matter. `pinned_provider` is what was chosen before the game began; `providers_used`
 * is what actually answered. They should be the same single name — and before pinning existed they
 * were not: one 80-ply game was served by two different endpoints, so its numbers describe a blend
 * that cannot be reproduced (ADR-0015). A drift warning is louder than a footnote because a reader
 * comparing two rows deserves to know one of them is not a clean measurement.
 */
function Endpoint({ player }: { player: Player }) {
  const used = player.providers_used;
  const pinned = player.pinned_provider;
  const drifted = used.length > 1 || (pinned !== null && used.length === 1 && used[0] !== pinned);

  if (!pinned && used.length === 0 && !player.quantization) return null;

  return (
    <p className="flex flex-wrap items-center gap-1">
      {player.quantization && (
        <span
          title="the precision this seat played at — its own leaderboard entry"
          className="border border-good/40 px-1 py-px font-mono text-[8.5px] uppercase tracking-wider text-good"
        >
          {player.quantization}
        </span>
      )}

      <span
        className="font-mono text-[8.5px] text-ink-faint"
        title={pinned ? "endpoint pinned before the game started" : "endpoint that served this seat"}
      >
        {used.length > 0 ? used.join(" + ") : pinned}
      </span>

      {drifted && (
        <span
          title={`Pinned to ${pinned ?? "nothing"} but served by ${used.join(", ")}. This result mixes endpoints and is not reproducible.`}
          className="border border-bad-deep px-1 py-px font-mono text-[8.5px] uppercase tracking-wider text-bad"
        >
          mixed endpoints
        </span>
      )}
    </p>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good" | "bad";
}) {
  const colour = tone === "bad" ? "text-bad" : tone === "good" ? "text-good" : "text-ink";
  return (
    <div className="min-w-0 bg-surface px-1.5 py-1">
      <dt className="truncate font-mono text-[8px] uppercase tracking-[0.08em] text-ink-faint">
        {label}
      </dt>
      <dd className={`tabular truncate font-mono text-[11px] ${colour}`}>{value}</dd>
    </div>
  );
}

function Row({
  label,
  value,
  muted,
  title,
}: {
  label: string;
  value: string;
  muted?: boolean;
  title?: string;
}) {
  return (
    <div className="tabular flex justify-between gap-2 font-mono text-[11px] text-ink-dim">
      <span title={title} className={title ? "cursor-help" : undefined}>
        {label}
      </span>
      <span className={muted ? "text-ink-faint" : "text-ink"}>{value}</span>
    </div>
  );
}

