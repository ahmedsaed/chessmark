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
      <div className="flex flex-col gap-1.5 border border-line bg-surface-2 p-3">
        <Row label="Move" value={String(Math.ceil((activePly ?? game.ply_count) / 2) || 1)} />
        <Row
          label="Plies"
          value={
            activePly === undefined
              ? String(game.ply_count)
              : `${activePly} / ${game.ply_count}`
          }
        />
        <Row label="Status" value={game.status} />
        {/* A game with a person in it can never be ranked — a person is not a contestant — so the
            row would read "no" for the whole game and tell the player nothing they did not choose.
            Whether chat is on is still worth stating, because they chose that too. */}
        {hasHuman ? (
          <Row label="Talk" value={game.trash_talk_enabled ? "on" : "off"} muted />
        ) : (
          <Row
            label="Ranked"
            value={game.is_ranked ? "yes" : `no · talk ${game.trash_talk_enabled ? "on" : "off"}`}
            muted
          />
        )}
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

      <div className="flex flex-col gap-1.5 border border-line bg-surface-2 p-3">
        <Label>Spend</Label>
        <Row label="Total" value={usd(game.total_cost_usd)} />
        <Row label="Cap" value={game.max_usd ? usd(game.max_usd) : "none"} muted />
        <Row label="Tokens" value={game.total_tokens.toLocaleString()} muted />
      </div>

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
    <div className="flex flex-col gap-2 border border-line bg-surface-2 p-3">
      <Label>Event</Label>

      <Link
        href={`/tournaments/${tournament.slug}`}
        className="truncate font-mono text-xs text-accent hover:underline"
      >
        {tournament.name}
      </Link>

      <p className="font-mono text-[9.5px] text-ink-faint">
        <span
          title={
            tournament.format === "pool"
              ? "a pool numbers one round per game, so this is the Nth pairing it scheduled"
              : undefined
          }
        >
          round {tournament.round_number}
        </span>
        {tournament.era && (
          <>
            {" · "}
            <span title="the prompt and tool-schema majors this game was played under — results are comparable within an era, not across one">
              era {tournament.era}
            </span>
          </>
        )}
        {" · "}
        {entrants} entrants
      </p>

      <div className="flex flex-col gap-px border border-line-soft bg-line-soft">
        {seats.map((seat) => (
          <SeatRow key={seat.colour} seat={seat} byRating={byRating} entrants={entrants} />
        ))}
      </div>

      <p className="font-mono text-[8.5px] leading-relaxed text-ink-faint">
        {byRating ? "Rating" : "Score"} and place in this {tournament.era ? "era" : "event"}, as
        they stand now — not as they stood when this game was played.
      </p>
    </div>
  );
}

/**
 * One seat's line in the Event card.
 *
 * Built like a `PlayerCard` rather than a table row so the two read as a pair: the same colour
 * swatch, the same name treatment, the same stat grid underneath. A reader scanning down the rail
 * meets each model twice and should recognise it the second time.
 *
 * A seat with no `place` is a seat the table does not carry — a human, or a model seated outside
 * the field. It keeps its row and says so, because an absent row would read as a rendering fault.
 */
function SeatRow({
  seat,
  byRating,
  entrants,
}: {
  seat: SeatStanding;
  byRating: boolean;
  entrants: number;
}) {
  return (
    <div className="flex flex-col gap-1.5 bg-surface px-2 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <i
          aria-hidden
          className={`block h-2 w-2 flex-none border border-line ${
            seat.colour === "white" ? "bg-piece-white" : "bg-piece-black"
          }`}
        />
        <b className="truncate font-mono text-[10.5px] font-normal text-ink">
          {seat.display_name}
        </b>
        {!seat.in_field && (
          <span
            title="no longer in this event's field — it keeps its record but gets no new pairings"
            className="ml-auto flex-none cursor-help font-mono text-[8.5px] uppercase tracking-wider text-ink-faint"
          >
            left
          </span>
        )}
      </div>

      {seat.place === null ? (
        <p className="font-mono text-[9.5px] text-ink-faint">not an entrant in this event</p>
      ) : (
        <dl className="grid grid-cols-3 gap-2">
          <Cell label="Place" value={`${seat.place} / ${entrants}`} />
          <Cell
            label={byRating ? "Rating" : "Score"}
            value={
              byRating
                ? seat.rating === null
                  ? "unrated"
                  : `${Math.round(seat.rating)}${seat.rating_provisional ? "?" : ""}`
                : String(seat.score)
            }
            title={
              byRating && seat.rating_provisional
                ? "still too few games for this rating to be read as a placing"
                : undefined
            }
          />
          <Cell label="Record" value={`${seat.wins}-${seat.draws}-${seat.losses}`} />
        </dl>
      )}
    </div>
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
      className={`relative flex flex-col gap-2 overflow-hidden border p-3 ${
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
      <p className="truncate font-mono text-[9.5px] text-ink-faint">
        {player.model ?? player.kind}
      </p>

      <Endpoint player={player} />

      <dl className="grid grid-cols-2 gap-px border border-line-soft bg-line-soft">
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
    <div className="bg-surface px-2 py-1.5">
      <dt className="font-mono text-[8.5px] uppercase tracking-[0.12em] text-ink-faint">
        {label}
      </dt>
      <dd className={`tabular mt-0.5 font-mono text-xs ${colour}`}>{value}</dd>
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

function Label({ children }: { children: React.ReactNode }) {
  return (
    <p className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-ink-faint">
      {children}
    </p>
  );
}
