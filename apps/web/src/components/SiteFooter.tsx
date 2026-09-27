/**
 * The site footer.
 *
 * Server component — nothing here is interactive. It carries the two claims the project is
 * actually making, because a visitor who has just read a rating is exactly the person who should
 * be told how it was produced and what it cannot tell them.
 */

import Link from "next/link";

import { SUPPORT_EMAIL } from "@/components/LegalPage";
import { footerNav, legalNav, siteName } from "@/lib/site";

export function SiteFooter() {
  return (
    <footer className="mt-auto border-t border-line bg-surface/40">
      <div className="mx-auto flex w-full max-w-[2200px] flex-col gap-6 px-5 py-8 sm:flex-row sm:items-start sm:justify-between">
        <div className="max-w-prose">
          <p className="font-mono text-data uppercase tracking-[0.2em] text-accent">
            {siteName}
          </p>
          <p className="mt-2 text-sm leading-relaxed text-ink-dim">
            A benchmark of long-horizon, tool-mediated, adversarial reliability — that happens to
            be watchable. Ratings come from ranked games only, and every number links to the raw
            provider payload that produced it.
          </p>
        </div>

        <nav aria-label="Footer" className="flex flex-col gap-2">
          {footerNav.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="font-mono text-data uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-accent"
            >
              {link.label}
            </Link>
          ))}
        </nav>
      </div>
      <nav
        aria-label="Policies"
        className="mx-auto flex w-full max-w-[2200px] flex-wrap gap-x-5 gap-y-2 border-t border-line/60 px-5 py-4"
      >
        {legalNav.map((link) => (
          <Link key={link.href} href={link.href} className={QUIET}>
            {link.label}
          </Link>
        ))}
        {/* A way to reach a person, one click from any page: what a buyer with a problem needs,
            and what a payment processor's review checks for. */}
        <a href={`mailto:${SUPPORT_EMAIL}`} className={QUIET}>
          Contact
        </a>
      </nav>
    </footer>
  );
}

const QUIET =
  "font-mono text-meta uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-accent";
