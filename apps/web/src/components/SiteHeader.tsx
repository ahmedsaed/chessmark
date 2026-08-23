"use client";

/**
 * The site header.
 *
 * Every page used to hand-roll a `← Chessmark` link, which is a back button rather than
 * navigation: from a game page there was no way to reach the leaderboard without going home
 * first, and `AccountBar` was mounted only on the landing page, so signing out was impossible
 * from anywhere else.
 *
 * Full-bleed and sticky. The inner container matches the game page's width rather than the
 * lobby's, because the game page is the one people sit on.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { AccountBar } from "@/components/AccountBar";
import { apiUrl } from "@/lib/api";
import { primaryNav, siteName } from "@/lib/site";

export function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-ground/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 w-full max-w-[2200px] items-center gap-6 px-5">
        <Link
          href="/"
          className="flex flex-none items-center gap-2.5 transition-opacity hover:opacity-80"
          aria-label={`${siteName} home`}
        >
          <Mark />
          <span className="font-mono text-xs uppercase tracking-[0.24em] text-accent">
            {siteName}
          </span>
        </Link>

        <nav aria-label="Primary" className="flex items-center gap-1">
          {primaryNav.map((link) => {
            const active = link.match ? link.match(pathname) : pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={`px-2.5 py-1 font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
                  active ? "text-ink" : "text-ink-faint hover:text-ink-dim"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <AccountBar apiUrl={apiUrl} />
      </div>
    </header>
  );
}

/**
 * A 3×3 corner of a board. Small enough to read at 18px, and it makes the tab and the header
 * say "chess" before the word does.
 */
function Mark() {
  return (
    <svg width="18" height="18" viewBox="0 0 3 3" aria-hidden className="flex-none">
      <rect width="3" height="3" fill="var(--color-sq-dark)" />
      <rect x="1" y="0" width="1" height="1" fill="var(--color-sq-light)" />
      <rect x="0" y="1" width="1" height="1" fill="var(--color-sq-light)" />
      <rect x="2" y="1" width="1" height="1" fill="var(--color-sq-light)" />
      <rect x="1" y="2" width="1" height="1" fill="var(--color-sq-light)" />
    </svg>
  );
}
