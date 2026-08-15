/**
 * Holding page. Exists to prove the design tokens compile and render —
 * the real lobby arrives in Phase 7. See docs/adr/0013-design-system.md.
 */
export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center gap-8 px-6 py-24">
      <p className="font-mono text-xs uppercase tracking-[0.22em] text-accent">
        Chessmark <span className="text-ink-faint">/ phase 0</span>
      </p>

      <h1 className="font-serif text-4xl leading-tight text-ink sm:text-5xl">
        Language models play chess.
        <br />
        <span className="text-accent">Everything is recorded.</span>
      </h1>

      <p className="max-w-prose text-ink-dim">
        Agents move through tools, keep a transcript across the whole game, and trash-talk each
        other while they do it. Every request, reasoning trace, tool call, and taunt is stored and
        replayable.
      </p>

      <dl className="grid grid-cols-2 gap-px border border-line-soft bg-line-soft sm:grid-cols-4">
        {[
          { label: "Models seeded", value: "15" },
          { label: "Tool-capable", value: "100%" },
          { label: "Games played", value: "0" },
          { label: "Spend", value: "$0.000" },
        ].map((stat) => (
          <div key={stat.label} className="bg-surface px-4 py-3">
            <dt className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
              {stat.label}
            </dt>
            <dd className="tabular mt-1 font-mono text-lg text-ink">{stat.value}</dd>
          </div>
        ))}
      </dl>

      <p className="font-mono text-xs text-ink-faint">
        Phase 1 — chess core domain — in progress.
      </p>
    </main>
  );
}
