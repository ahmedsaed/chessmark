/**
 * The site footer.
 *
 * Server component — nothing here is interactive. It carries the two claims the project is
 * actually making, because a visitor who has just read a rating is exactly the person who should
 * be told how it was produced and what it cannot tell them.
 */

import Link from "next/link";

import { footerNav, siteName } from "@/lib/site";

export function SiteFooter() {
  return (
    <footer className="mt-auto border-t border-line bg-surface/40">
      <div className="mx-auto flex w-full max-w-[2200px] flex-col gap-6 px-5 py-8 sm:flex-row sm:items-start sm:justify-between">
        <div className="max-w-prose">
          <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-accent">
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
              className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-accent"
            >
              {link.label}
            </Link>
          ))}
        </nav>
      </div>
    </footer>
  );
}
