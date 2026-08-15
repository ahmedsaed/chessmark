/**
 * The left rail: who is playing, how they are doing, and what it costs.
 *
 * `illegal` sits on the face of the card rather than in a methodology page. It is the benchmark's
 * most interesting number and the whole reason the project exists.
 */

import type { GameDetail, Player } from "@/lib/types";

function usd(value: string): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  if (amount === 0) return "$0.000";
  return amount < 0.001 ? `$${amount.toFixed(6)}` : `$${amount.toFixed(3)}`;
}

function cacheRate(player: Player): string {
  if (!player.prompt_tokens) return "—";
  return `${Math.round((player.cached_tokens / player.prompt_tokens) * 100)}%`;
}

export function StatsRail({
  game,
  toMove,
  moves,
}: {
  game: GameDetail;
  toMove: "white" | "black" | null;
  moves: string[];
}) {
  const white = game.players.find((p) => p.colour === "white");
  const black = game.players.find((p) => p.colour === "black");

  return (
    <aside aria-label="Game statistics" className="flex min-w-0 flex-col gap-2.5">
      <div className="flex flex-col gap-1.5 border border-line bg-surface-2 p-3">
        <Row label="Move" value={String(Math.ceil(moves.length / 2) || 1)} />
        <Row label="Plies" value={String(moves.length)} />
        <Row label="Status" value={game.status} />
        <Row
          label="Ranked"
          value={game.is_ranked ? "yes" : `no · talk ${game.trash_talk_enabled ? "on" : "off"}`}
          muted
        />
      </div>

      {white && <PlayerCard player={white} active={toMove === "white"} />}
      {black && <PlayerCard player={black} active={toMove === "black"} />}

      <div className="flex flex-col gap-1.5 border border-line bg-surface-2 p-3">
        <Label>Spend</Label>
        <Row label="Total" value={usd(game.total_cost_usd)} />
        <Row label="Cap" value={game.max_usd ? usd(game.max_usd) : "none"} muted />
        <Row label="Tokens" value={game.total_tokens.toLocaleString()} muted />
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-2 border border-line bg-surface-2 p-3">
        <Label>Moves</Label>
        <ol className="tabular grid grid-cols-[1.75rem_1fr_1fr] gap-x-2 gap-y-0.5 overflow-y-auto font-mono text-[11.5px] text-ink-dim">
          {Array.from({ length: Math.ceil(moves.length / 2) }, (_, index) => (
            <li key={index} className="contents">
              <span className="text-ink-faint">{index + 1}</span>
              <span className="text-ink">{moves[index * 2] ?? ""}</span>
              <span className="text-ink">{moves[index * 2 + 1] ?? ""}</span>
            </li>
          ))}
        </ol>
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

      <dl className="grid grid-cols-2 gap-px border border-line-soft bg-line-soft">
        <Stat label="Tokens" value={player.prompt_tokens.toLocaleString()} />
        <Stat label="Cached" value={cacheRate(player)} />
        <Stat label="Cost" value={usd(player.total_cost_usd)} />
        <Stat
          label="Illegal"
          value={String(player.illegal_attempts)}
          tone={player.illegal_attempts > 0 ? "bad" : "good"}
        />
      </dl>
    </div>
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
