"use client";

/**
 * Sign-in state and today's allowance.
 *
 * Watching needs no account (AUTH-02), so this is a quiet corner of the header rather than a wall:
 * signed out, it offers a sign-in; signed in, it shows the credit balance. A credit is a unit of
 * granted play, spent to start a game, and it does **not** refill (ADR-0016) — so a reader at zero
 * needs to know that asking is the only thing that changes it, which is what the tooltip says. Showing the allowance *before* it is spent matters: discovering your limit
 * by being refused is a bad way to learn it, especially when the refusal costs you the game you
 * were trying to start.
 *
 * Renders nothing when Clerk is not configured, which is how the project runs locally.
 */

import { Show, SignInButton, SignUpButton, UserButton, useAuth } from "@clerk/nextjs";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { clerkEnabled } from "@/components/AuthProvider";
import type { Me } from "@/lib/types";

export function AccountBar({ apiUrl }: { apiUrl: string }) {
  if (!clerkEnabled) return null;
  return <Bar apiUrl={apiUrl} />;
}

function Bar({ apiUrl }: { apiUrl: string }) {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const [me, setMe] = useState<Me | null>(null);

  /* Refetched on every navigation, because the balance is stale the moment a game starts and this
     component never unmounts to notice. It lives in the root layout, so a client-side push to
     `/games/{id}` re-renders the page under it and leaves the header showing a number read at
     first paint — a person spent two credits, watched the game open, and the header still said
     ten until they reloaded.

     Keyed on the path rather than pushed to from the form: starting a game is the only thing that
     spends credits and it always navigates, so the navigation *is* the signal, and the header
     stays right without the two components having to know about each other. */
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
    <span className="ml-auto flex items-center gap-3">
      {/* `Show` replaced `SignedIn`/`SignedOut` in @clerk/nextjs Core 3 — the older components are
          still exported and throw at render time, so the swap is not optional. */}
      <Show when="signed-out">
        <SignInButton mode="modal">
          <button
            type="button"
            className="border border-line bg-surface px-2 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-faint transition-colors hover:border-accent-dim hover:text-ink"
          >
            sign in
          </button>
        </SignInButton>
        <SignUpButton mode="modal">
          <button
            type="button"
            className="border border-accent-deep bg-accent px-2 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-on-accent transition-colors hover:bg-accent-dim"
          >
            sign up
          </button>
        </SignUpButton>
      </Show>

      <Show when="signed-in">
        {/* `me` is only rendered while signed in, so a stale readout from a previous session
            cannot appear — which is why signing out needs no cleanup here. */}
        {me && (
          <span
            className="tabular font-mono text-[10px] text-ink-faint"
            title={
              me.credit_balance === 0
                ? "No credits. An administrator grants them."
                : `$${Number(me.usd_spent_today).toFixed(4)} spent today`
            }
          >
            {me.credit_balance} credit
            {me.credit_balance === 1 ? "" : "s"}
          </span>
        )}
        <UserButton />
      </Show>
    </span>
  );
}
