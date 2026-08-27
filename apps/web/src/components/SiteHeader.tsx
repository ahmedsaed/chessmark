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
 *
 * **Two layouts, one nav.** Six links at 11px with 0.14em tracking need about 380px, so on a phone
 * they overflowed the bar and pushed the account controls off the right edge — the site's own
 * sign-in button, unreachable. Below `md` the links move into a disclosure panel under the bar and
 * the trigger takes their place.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { AccountBar } from "@/components/AccountBar";
import { apiUrl } from "@/lib/api";
import { primaryNav, siteName } from "@/lib/site";

export function SiteHeader() {
  const pathname = usePathname();
  /* Open-for-which-path rather than a boolean, so navigating closes the panel without an effect
     that calls `setState` — which is a cascading render, and which the lint rule rightly refuses.
     The panel is not a route, so nothing else would close it, and arriving on a new page with the
     menu still over it reads as the click having failed. */
  const [openFor, setOpenFor] = useState<string | null>(null);
  const open = openFor === pathname;

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-ground/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 w-full max-w-[2200px] items-center gap-3 px-5 md:gap-6">
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

        <nav aria-label="Primary" className="hidden items-center gap-1 md:flex">
          {primaryNav.map((link) => (
            <NavLink key={link.href} link={link} pathname={pathname} />
          ))}
        </nav>

        <button
          type="button"
          onClick={() => setOpenFor(open ? null : pathname)}
          aria-expanded={open}
          aria-controls="primary-nav-panel"
          aria-label="Navigation"
          className="ml-auto flex-none border border-line px-2 py-1 font-mono text-[11px] uppercase tracking-[0.14em] text-ink-faint transition-colors hover:border-accent-dim hover:text-ink md:hidden"
        >
          {open ? "close" : "menu"}
        </button>

        <AccountBar apiUrl={apiUrl} />
      </div>

      {/* Under the bar rather than over the page: a sticky header is already the top of the
          viewport, so an overlay would cover the content for no gain and need dismissing. */}
      {open && (
        <nav
          id="primary-nav-panel"
          aria-label="Primary"
          className="flex flex-col border-t border-line bg-ground md:hidden"
        >
          {primaryNav.map((link) => (
            <NavLink key={link.href} link={link} pathname={pathname} stacked />
          ))}
        </nav>
      )}
    </header>
  );
}

function NavLink({
  link,
  pathname,
  stacked = false,
}: {
  link: (typeof primaryNav)[number];
  pathname: string;
  stacked?: boolean;
}) {
  const active = link.match ? link.match(pathname) : pathname === link.href;
  return (
    <Link
      href={link.href}
      aria-current={active ? "page" : undefined}
      className={`font-mono text-[11px] uppercase tracking-[0.14em] transition-colors ${
        stacked ? "border-b border-line-soft px-5 py-3" : "px-2.5 py-1"
      } ${active ? "text-ink" : "text-ink-faint hover:text-ink-dim"}`}
    >
      {link.label}
    </Link>
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
