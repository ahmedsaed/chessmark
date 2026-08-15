import Link from "next/link";
import { notFound } from "next/navigation";

import { LiveGame } from "@/components/LiveGame";
import { apiUrl, getGame, listEvents } from "@/lib/api";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: PageProps<"/games/[id]">) {
  // `params` is a Promise in Next.js 16.
  const { id } = await params;
  const game = await getGame(id);
  if (!game) return { title: "Game not found · Chessmark" };

  const white = game.players.find((p) => p.colour === "white")?.display_name ?? "White";
  const black = game.players.find((p) => p.colour === "black")?.display_name ?? "Black";

  return {
    title: `${white} vs ${black} · Chessmark`,
    description:
      game.status === "finished"
        ? `${white} vs ${black} — ${game.result} by ${game.termination}.`
        : `${white} vs ${black}, live.`,
  };
}

export default async function GamePage({ params }: PageProps<"/games/[id]">) {
  const { id } = await params;
  const game = await getGame(id);

  if (!game) notFound();

  const events = await listEvents(id);

  return (
    <main className="mx-auto w-full max-w-[1400px] px-5 py-8">
      <Link
        href="/"
        className="mb-5 inline-block font-mono text-[11px] uppercase tracking-[0.16em] text-ink-faint transition-colors hover:text-accent"
      >
        ← Chessmark
      </Link>
      <LiveGame game={game} apiUrl={apiUrl} initialEvents={events} />
    </main>
  );
}
