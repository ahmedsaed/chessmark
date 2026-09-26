"use client";

/**
 * Sign-in state and today's allowance.
 *
 * Watching needs no account (AUTH-02), so this is a quiet corner of the header rather than a wall:
 * signed out, it offers a sign-in; signed in, it shows the credit balance. Credit is dollars,
 * spent as a game plays at what each turn actually cost, and it does **not** refill (ADR-0052) —
 * so a reader at zero needs to know how it changes, which is what the tooltip says. Showing the
 * balance *before* it is spent matters: discovering your limit by being refused is a bad way to
 * learn it, especially when the refusal costs you the game you were trying to start.
 *
 * Renders nothing when Clerk is not configured, which is how the project runs locally.
 */

import { Show, useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { AccountMenu } from "@/components/AccountMenu";
import { clerkEnabled } from "@/components/AuthProvider";
import { useClerkMounted } from "@/components/ClerkGate";
import type { Me } from "@/lib/types";
import { canPay, formatBalance } from "@/lib/credit";

/**
 * The height every control in the header shares.
 *
 * Each one used to derive its own from padding plus whatever it contained: the nav trigger came to
 * 28px from a 14px glyph, `sign in` to 26.5px from its line-height, and the account card to 32px
 * from a 22px avatar. Three heights in one 56px bar, visibly unaligned on a phone where they sit
 * side by side. Stating it once is the only thing that keeps them equal as the contents change.
 */
export const CONTROL_HEIGHT = "h-7";

export function AccountBar({ apiUrl }: { apiUrl: string }) {
  /**
   * Whether a `ClerkProvider` is above this component — from context, not a prop.
   *
   * It used to be threaded down from the root layout, which made it the answer for the *request*
   * rather than for right now. `ClerkGate` can mount a provider the server never did, on a soft
   * navigation the layout never saw, and a prop would still have been reporting first paint. It is
   * the only fact this needs: Clerk is mounted when a session exists or the route is about
   * identity, so *not* mounted means *not signed in* here.
   */
  const clerkMounted = useClerkMounted();

  if (!clerkEnabled) return null;

  /**
   * **Without Clerk mounted there is no `Show` and no `useAuth` — and no need for either.**
   *
   * A signed-out reader on `/leaderboard` gets no Clerk at all now, so this has to draw the
   * signed-out bar from the cookie the server already read. It is the same two links, and it is
   * the whole reason the 87 KiB can stay unloaded: a header that needs a hook is a header that
   * needs the client.
   *
   */
  if (!clerkMounted) return <SignedOutLinks />;

  return <Bar apiUrl={apiUrl} />;
}

function SignedOutLinks() {
  return (
    <span className="flex shrink-0 items-center gap-2 md:ml-auto md:gap-3">
      <Link
        href="/sign-in"
        className={`${CONTROL_HEIGHT} inline-flex items-center whitespace-nowrap border border-line bg-surface px-2 font-mono text-meta uppercase tracking-[0.1em] text-ink-faint transition-colors hover:border-accent-dim hover:text-ink`}
      >
        sign in
      </Link>
      <Link
        href="/sign-up"
        className={`${CONTROL_HEIGHT} inline-flex items-center whitespace-nowrap border border-accent-deep bg-accent px-2 font-mono text-meta uppercase tracking-[0.1em] text-on-accent transition-colors hover:bg-accent-dim`}
      >
        sign up
      </Link>
    </span>
  );
}

function Bar({ apiUrl }: { apiUrl: string }) {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const [me, setMe] = useState<Me | null>(null);

  /* Refetched on every navigation, because this component lives in the root layout and never
     unmounts to notice a change. **It is a reading, not a live meter**: credit is spent turn by
     turn while a game plays (ADR-0052), so the number here is as of the last page a person opened,
     and a game in progress moves it underneath. Keyed on the path so the header is right whenever
     somebody goes to look at their games, without the two components knowing about each other. */
  const pathname = usePathname();

  useEffect(() => {
    if (!isSignedIn) return;

    let cancelled = false;
    (async () => {
      try {
        // The token goes to *our* API in an Authorization header and nowhere else. It is a
        // short-lived Clerk session token, never a key of ours — invariant 10 holds because the
        // client has no key to leak.
        const token = await getToken();
        const response = await fetch(`${apiUrl}/me`, {
          headers: { authorization: `Bearer ${token}`, accept: "application/json" },
        });
        if (response.ok && !cancelled) setMe((await response.json()) as Me);
      } catch {
        // A missing allowance readout is not worth an error state; the API refuses on its own.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [isSignedIn, getToken, apiUrl, pathname]);

  if (!isLoaded) return null;

  return (
    /* `shrink-0`: this is the one thing in the header that must not be compressed. It was
       shrinkable, so when the bar ran out of room the browser took the space from here and "sign
       in" wrapped to two lines — a 40px button in a row of 27px ones, and the site's own sign-in
       control the thing that gave way. Everything else in the bar is either fixed or droppable. */
    <span className="flex shrink-0 items-center gap-2 md:ml-auto md:gap-3">
      {/* `Show` replaced `SignedIn`/`SignedOut` in @clerk/nextjs Core 3 — the older components are
          still exported and throw at render time, so the swap is not optional. */}
      {/* **Links, not `SignInButton mode="modal"`.** The modal is a prebuilt Clerk component, and
          one of those anywhere puts 285 KiB of `@clerk/ui` on every route — `/about` and
          `/leaderboard` included, where nobody is signing in. A link costs nothing and goes to a
          page we own. `Show` stays: it is a *control* component, which is exactly what
          `prefetchUI={false}` is documented to support. */}
      <Show when="signed-out">
        <Link
          href="/sign-in"
          className={`${CONTROL_HEIGHT} inline-flex items-center whitespace-nowrap border border-line bg-surface px-2 font-mono text-meta uppercase tracking-[0.1em] text-ink-faint transition-colors hover:border-accent-dim hover:text-ink`}
        >
          sign in
        </Link>
        <Link
          href="/sign-up"
          className={`${CONTROL_HEIGHT} inline-flex items-center whitespace-nowrap border border-accent-deep bg-accent px-2 font-mono text-meta uppercase tracking-[0.1em] text-on-accent transition-colors hover:bg-accent-dim`}
        >
          sign up
        </Link>
      </Show>

      <Show when="signed-in">
        {/* `me` is only rendered while signed in, so a stale readout from a previous session
            cannot appear — which is why signing out needs no cleanup here. */}
        {me && (
          <span
            /* Hidden on a phone, where the header is already four items wide and the menu repeats
               it. Visible everywhere else: the number decides whether you can start a game, and
               one click away is worse than in front of you. */
            className="tabular hidden font-mono text-meta text-ink-faint sm:inline"
            title={
              canPay(me.balance_usd)
                ? `$${Number(me.usd_spent_today).toFixed(4)} spent today`
                : "No credit. An administrator grants it."
            }
          >
            {formatBalance(me.balance_usd)}
          </span>
        )}
        {/* Was `<UserButton />`, whose menu offered account management this site does not use and
            knew nothing about credit, and then a bare `profile` link that showed neither who you
            were signed in as nor a way out. `AccountMenu` is ours, built from hooks rather than
            from a prebuilt component, so it costs nothing on the routes nobody signs in on. */}
        <AccountMenu me={me} />
      </Show>
    </span>
  );
}
