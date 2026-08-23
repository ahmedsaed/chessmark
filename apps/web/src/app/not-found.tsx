import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto flex w-full max-w-[640px] flex-1 flex-col justify-center px-5 py-24">
      <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-ink-faint">404</p>
      <h1 className="mt-3 font-serif text-4xl leading-tight text-ink">Nothing here.</h1>
      <p className="mt-4 text-ink-dim">
        That page does not exist. A game id that was never played, or a link that has rotted.
      </p>
      <div className="mt-8 flex flex-wrap gap-3">
        <Link
          href="/"
          className="border border-accent-deep bg-accent px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.14em] text-on-accent transition-colors hover:bg-accent-dim"
        >
          Watch a game
        </Link>
        <Link
          href="/leaderboard"
          className="border border-line bg-surface px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.14em] text-ink-dim transition-colors hover:border-accent-dim hover:text-ink"
        >
          Leaderboard
        </Link>
      </div>
    </main>
  );
}
