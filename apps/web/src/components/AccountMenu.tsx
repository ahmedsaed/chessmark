"use client";

/**
 * The account control in the header: a face, a name, and two things you can do.
 *
 * **Built from hooks, not from `<UserButton />`.** Clerk's own menu is a prebuilt component, and
 * one of those anywhere puts 285 KiB of `@clerk/ui` on every route — `/about` included, where
 * nobody is signed in. `useUser` and `useClerk` are hooks and cost nothing beyond the core, so the
 * picture and the name are free; the menu is ours, in our type, and knows about credits, which
 * Clerk's never did.
 *
 * **The trap it re-introduces, written down because it was written down once before and removed.**
 * This is a collapsed disclosure that sorts *first* in the document on every signed-in page, so
 * `page.locator('button[aria-expanded="false"]')` in a browser test finds this and not whatever it
 * meant. Clicking it opens this menu over the page and every later click fails on an element it has
 * covered. Scope such selectors to what they are about — `getByTestId("turn")`, not the first
 * disclosure on the page. `docs/TESTING.md` carries it as a trap.
 */

import { useClerk, useUser } from "@clerk/nextjs";

import { CONTROL_HEIGHT } from "@/components/AccountBar";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { displayNameOf, initialsOf } from "@/lib/identity";
import type { Me } from "@/lib/types";

export function AccountMenu({ me }: { me: Me | null }) {
  const { user } = useUser();
  const { signOut } = useClerk();
  const router = useRouter();
  const menuId = useId();

  const [open, setOpen] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    // Focus goes back to the button that opened it, or a keyboard user is returned to the top of
    // the document and has to tab the whole header again to get anywhere.
    if (refocus) trigger.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close(true);
    };
    /* `pointerdown`, not `click`. A click listener fires after the mousedown has already moved
       focus, and on a control *inside* the menu the outside-check then races the item's own
       handler. Pointerdown settles it before anything else happens. */
    const onOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (panel.current?.contains(target) || trigger.current?.contains(target)) return;
      close(false);
    };

    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onOutside);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onOutside);
    };
  }, [open, close]);

  const name = displayNameOf(user);
  const email = user?.primaryEmailAddress?.emailAddress;
  const picture = !imageFailed && user?.imageUrl;

  return (
    <span className="relative">
      <button
        ref={trigger}
        type="button"
        onClick={() => setOpen((was) => !was)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        /* The shared 28px. The avatar sets its own size inside that rather than deciding the
           control's, which is how this came out 32px against a 28px trigger beside it. */
        className={`${CONTROL_HEIGHT} flex items-center gap-2 border border-line bg-surface pl-1 pr-2 text-ink-faint transition-colors hover:border-accent-dim hover:text-ink`}
      >
        {picture ? (
          /* A plain `<img>`, not `next/image`. This is a 22px avatar from Clerk's CDN: routing it
             through the optimiser would mean adding `img.clerk.com` to `remotePatterns` and a
             server round trip per header render, to optimise an image already smaller than the
             request that would fetch it. The rule is about LCP, and this element is never the
             largest contentful paint on any page. */
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={picture}
            alt=""
            width={22}
            height={22}
            className="h-5 w-5 shrink-0 rounded-full object-cover"
            onError={() => setImageFailed(true)}
          />
        ) : (
          <span
            aria-hidden
            className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-3 font-mono text-label text-ink-dim"
          >
            {initialsOf(name)}
          </span>
        )}
        {/* The name is dropped below `sm`, where the header has four other things in it and a
            30-character display name is what pushes the row into a second line. The picture and
            the accessible name below carry it. */}
        {/* Not `uppercase` like the header's other controls, and not `lowercase` as this first
            was: those are labels and this is somebody's name. "ada lovelace" is a typo, not a
            style. */}
        <span className="hidden max-w-[12ch] truncate font-mono text-meta sm:inline">
          {name}
        </span>
        <span className="sr-only">Account menu for {name}</span>
        <Chevron open={open} />
      </button>

      {open && (
        <div
          ref={panel}
          id={menuId}
          role="menu"
          aria-label="Account"
          /* `right-0`: the trigger is the last thing in the header, so a menu anchored left would
             hang off the edge of the viewport on a phone. */
          className="absolute right-0 z-50 mt-1 w-[13rem] border border-line bg-surface-2 py-1 shadow-lg"
        >
          <div className="border-b border-line-soft px-3 pb-2 pt-1.5">
            <p className="truncate font-mono text-meta text-ink">{name}</p>
            {email && <p className="truncate font-mono text-label text-ink-faint">{email}</p>}
            {/* Shown *instead of* the header chip, not beside it: the chip is hidden below `sm`
                and this is hidden above it, so the balance appears exactly once at every width.
                It has to appear somewhere — discovering your allowance by being refused is a bad
                way to learn it (ADR-0016) — and twice on one screen reads as two numbers. */}
            {me && (
              <p className="tabular mt-1 font-mono text-label text-ink-faint sm:hidden">
                {me.credit_balance} credit{me.credit_balance === 1 ? "" : "s"}
              </p>
            )}
          </div>

          {/* Both items close the menu themselves. Watching the pathname instead would mean
              setting state from an effect on every navigation in the app, for a menu that is shut
              on all but one of them. */}
          <Link
            href="/profile"
            role="menuitem"
            onClick={() => setOpen(false)}
            className={ITEM}
          >
            Profile
          </Link>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              void signOut(() => router.push("/"));
            }}
            className={`${ITEM} w-full text-left hover:text-bad`}
          >
            Sign out
          </button>
        </div>
      )}
    </span>
  );
}

const ITEM =
  "block px-3 py-2 font-mono text-meta uppercase tracking-[0.1em] text-ink-faint transition-colors hover:bg-surface-3 hover:text-ink";

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 8 5"
      className={`h-[5px] w-2 shrink-0 fill-current transition-transform ${open ? "rotate-180" : ""}`}
    >
      <path d="M0 0h8L4 5z" />
    </svg>
  );
}
