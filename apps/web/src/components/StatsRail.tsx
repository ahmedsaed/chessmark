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

import type { GameDetail, Player } from "@/lib/types";

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
}: {
  game: GameDetail;
  toMove: "white" | "black" | null;
  /** Replay only: how far through the game the board currently is. */
  activePly?: number;
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

      {white && <PlayerCard player={white} active={toMove === "white"} />}
      {black && <PlayerCard player={black} active={toMove === "black"} />}

      <div className="flex flex-col gap-1.5 border border-line bg-surface-2 p-3">
        <Label>Spend</Label>
        <Row label="Total" value={usd(game.total_cost_usd)} />
        <Row label="Cap" value={game.max_usd ? usd(game.max_usd) : "none"} muted />
        <Row label="Tokens" value={game.total_tokens.toLocaleString()} muted />
      </div>

    </aside>
  );
}

function PlayerCard({ player, active }: { player: Player; active: boolean }) {
  return (
    <div
      className={`flex flex-col gap-2 border p-3 ${
        active ? "border-accent-deep bg-surface-3" : "border-line bg-surface-2"
      }`}
    >
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

function Row({ label, value, muted }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className="tabular flex justify-between gap-2 font-mono text-[11px] text-ink-dim">
      <span>{label}</span>
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
