/**
 * The frame shared by the terms, the privacy policy and the refund policy.
 *
 * One component so the three read as one set: the same width as `/about`, the same section
 * headings, and a date on every one of them — a policy without the date it took effect cannot be
 * held to, by the reader or by us.
 */

import Link from "next/link";

/** Where every policy sends a question, a deletion request or a broken-game report. */
export const SUPPORT_EMAIL = "support@merope.dev";

export function LegalPage({
  title,
  updated,
  summary,
  children,
}: {
  title: string;
  /** As written in the page, e.g. "27 September 2026". */
  updated: string;
  summary: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <main className="mx-auto w-full max-w-[760px] flex-1 px-5 py-12">
      <h1 className="font-serif text-4xl leading-tight text-ink">{title}</h1>
      <p className="mt-3 font-mono text-meta uppercase tracking-[0.14em] text-ink-faint">
        Last updated {updated}
      </p>
      <div className="mt-6 text-lg leading-relaxed text-ink-dim">{summary}</div>
      {children}
      <p className="mt-12 border-t border-line pt-6 text-sm text-ink-dim">
        Questions about this page go to <SupportEmail />. See also the{" "}
        <InlineLink href="/terms">terms of service</InlineLink>, the{" "}
        <InlineLink href="/privacy">privacy policy</InlineLink> and the{" "}
        <InlineLink href="/refunds">refund policy</InlineLink>.
      </p>
    </main>
  );
}

export function Clause({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-10">
      <h2 className="font-mono text-meta uppercase tracking-[0.18em] text-ink-faint">{title}</h2>
      <div className="mt-4 flex flex-col gap-4 leading-relaxed text-ink-dim">{children}</div>
    </section>
  );
}

export function Points({ children }: { children: React.ReactNode }) {
  return <ul className="flex list-disc flex-col gap-2 pl-5 marker:text-ink-faint">{children}</ul>;
}

export function InlineLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link href={href} className="text-accent underline underline-offset-4">
      {children}
    </Link>
  );
}

export function SupportEmail() {
  return (
    <a href={`mailto:${SUPPORT_EMAIL}`} className="text-accent underline underline-offset-4">
      {SUPPORT_EMAIL}
    </a>
  );
}
