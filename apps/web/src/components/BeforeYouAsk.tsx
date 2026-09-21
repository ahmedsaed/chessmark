/**
 * The three objections a sceptical visitor has before they leave.
 *
 * **Not an FAQ.** `/about` and `/methodology` own nine sections between them — what is measured,
 * what is recorded, what it cannot tell you, which games count, where it is weak — and answering
 * any of that a second time here would be two copies to keep true, with the lobby's copy the one
 * nobody remembers to update. Each line is therefore a sentence and a door: the shortest honest
 * answer, and the page that owns the long one.
 *
 * The three are the ones that decide whether a reader believes the rest of the page: whether the
 * games are real, why their favourite model is not on the board, and whether the numbers can be
 * checked. A benchmark that leaves those unanswered above the fold is asking to be trusted rather
 * than checked.
 */

import Link from "next/link";

const ASKED = [
  {
    question: "Do they really play?",
    answer:
      "They move by calling tools, and the server is the only authority on the board — it validates every move and rejects the illegal ones. A model cannot corrupt a game record.",
    href: "/about",
    where: "About",
  },
  {
    question: "Why isn't my model here?",
    answer:
      "A rating only moves on games run in one fixed, versioned configuration. Anything else — a different prompt, trash talk, an unpinned endpoint — is recorded and replayable, and never counted.",
    href: "/methodology",
    where: "Methodology",
  },
  {
    question: "Can I check the numbers?",
    answer:
      "Every rating reaches the games behind it, every game keeps the raw request and response of each turn, and every game the ranking excluded is listed with its reason.",
    href: "/leaderboard",
    where: "Leaderboard",
  },
] as const;

export function BeforeYouAsk() {
  return (
    <section className="mt-14">
      <div className="mb-4 flex items-baseline gap-3">
        <h2 className="font-mono text-meta uppercase tracking-[0.18em] text-ink-faint">
          Before you ask
        </h2>
        <span className="h-px flex-1 bg-line-soft" aria-hidden />
      </div>

      {/* Three across on a wide screen, stacked on a phone — one row rather than three, because
          the whole point of the strip is that it is short. */}
      <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {ASKED.map((item) => (
          <div
            key={item.question}
            className="flex flex-col gap-2 border border-line-soft bg-surface p-4"
          >
            <dt className="font-mono text-data text-ink">{item.question}</dt>
            <dd className="flex flex-col gap-2 text-sm leading-relaxed text-ink-dim">
              <span>{item.answer}</span>
              <Link
                href={item.href}
                prefetch={false}
                className="mt-auto font-mono text-meta uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-accent"
              >
                {item.where} →
              </Link>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
