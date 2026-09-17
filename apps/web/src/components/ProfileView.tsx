"use client";

/**
 * The account screen, in the site's own type and colour.
 *
 * Built on `useUser` and `useClerk` rather than Clerk's `<UserProfile />` for the reason the whole
 * of this change exists: one prebuilt component anywhere puts 285 KiB of `@clerk/ui` on every
 * route. `useUser` and `useClerk` are hooks, not components, and cost nothing beyond the core.
 *
 * **What it deliberately does not do.** Clerk's own profile manages emails, passwords, connected
 * accounts, sessions and MFA. This manages a display name and signs you out, because those are the
 * two things this site needs, and re-implementing account recovery is how people get locked out.
 * Anything else stays with the provider that is good at it: a person who needs to change their
 * email does it wherever they signed in.
 *
 * The allowance comes from *our* API, not from Clerk — credits are a Chessmark concept
 * (ADR-0016), granted by an administrator and spent to start a game.
 */

import { useAuth, useClerk, useUser } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { clerkEnabled } from "@/components/AuthProvider";
import type { Me } from "@/lib/types";

export function ProfileView({ apiUrl }: { apiUrl: string }) {
  /* Clerk absent is the local and CI configuration, and this page has nothing to say without it.
     Rendering the empty shell would be worse than saying so. */
  if (!clerkEnabled) {
    return <Shell>Accounts are not configured on this deployment.</Shell>;
  }
  return <Profile apiUrl={apiUrl} />;
}

function Profile({ apiUrl }: { apiUrl: string }) {
  const { isLoaded, isSignedIn, user } = useUser();
  const { getToken } = useAuth();
  const { signOut } = useClerk();
  const router = useRouter();

  const [me, setMe] = useState<Me | null>(null);
  /* `null` means "not edited yet", so the field shows whatever Clerk currently holds without an
     effect writing state on every render of the user object. */
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isSignedIn) return;

    let cancelled = false;
    (async () => {
      try {
        /* The token goes to *our* API in an Authorization header and nowhere else — a
           short-lived Clerk session token, never a key of ours (invariant 10). */
        const token = await getToken();
        const response = await fetch(`${apiUrl}/me`, {
          headers: { authorization: `Bearer ${token}`, accept: "application/json" },
        });
        if (response.ok && !cancelled) setMe((await response.json()) as Me);
      } catch {
        /* The allowance is worth showing and not worth an error state — the API refuses on its own
           when the balance is spent, which is the only moment it actually matters. */
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [isSignedIn, getToken, apiUrl]);

  const name = draft ?? user?.username ?? user?.firstName ?? "";

  const save = useCallback(async () => {
    if (!user) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await user.update({ username: name.trim() });
      setSaved(true);
    } catch (cause) {
      /* Clerk's field errors are structured; the first message is the one a person can act on
         ("that username is taken"), and the rest repeat it in other words. */
      setError(messageOf(cause));
    } finally {
      setSaving(false);
    }
  }, [user, name]);

  if (!isLoaded) return <Shell>Loading…</Shell>;

  if (!isSignedIn || !user) {
    return (
      <Shell>
        <p className="text-sm text-ink-dim">You are not signed in.</p>
        <Link
          href="/sign-in?redirect=/profile"
          className="mt-4 inline-flex border border-accent-deep bg-accent px-3 py-1.5 font-mono text-meta uppercase tracking-[0.1em] text-on-accent transition-colors hover:bg-accent-dim"
        >
          Sign in
        </Link>
      </Shell>
    );
  }

  return (
    <main className="mx-auto w-full max-w-[760px] px-5 py-10">
      <h1 className="font-serif text-4xl text-ink">Profile</h1>
      <p className="mt-2 text-sm text-ink-dim">
        {user.primaryEmailAddress?.emailAddress ?? "No email on this account"}
      </p>

      <section className="mt-10">
        <Heading>Display name</Heading>
        <p className="mt-2 text-sm text-ink-dim">
          What a game shows when you hold a seat. Models are named by their model id; you are named
          by this.
        </p>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="display-name">
            Display name
          </label>
          <input
            id="display-name"
            value={name}
            onChange={(event) => {
              setDraft(event.target.value);
              setSaved(false);
            }}
            className="min-w-0 flex-1 border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus-visible:border-accent"
            placeholder="unnamed"
            autoComplete="username"
          />
          <button
            type="button"
            onClick={save}
            disabled={saving || name.trim().length === 0}
            className="border border-accent-deep bg-accent px-4 py-2 font-mono text-meta uppercase tracking-[0.1em] text-on-accent transition-colors hover:bg-accent-dim disabled:opacity-40"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>

        {error && (
          <p role="alert" className="mt-2 font-mono text-meta text-bad">
            {error}
          </p>
        )}
        {saved && !error && (
          <p role="status" className="mt-2 font-mono text-meta text-good">
            Saved.
          </p>
        )}
      </section>

      <section className="mt-12">
        <Heading>Allowance</Heading>
        <p className="mt-2 text-sm text-ink-dim">
          A credit is one game you can start. Credits are granted by an administrator and do not
          refill, so the number below is the whole of what you have.
        </p>

        <dl className="mt-5 flex flex-wrap gap-10">
          <Stat label="credits" value={me ? String(me.credit_balance) : "—"} />
          <Stat label="games today" value={me ? String(me.games_started_today) : "—"} />
          <Stat
            label="spent today"
            value={me ? `$${Number(me.usd_spent_today).toFixed(4)}` : "—"}
          />
        </dl>
      </section>

      <section className="mt-12 border-t border-line-soft pt-8">
        <button
          type="button"
          onClick={() => signOut(() => router.push("/"))}
          className="border border-line bg-surface px-4 py-2 font-mono text-meta uppercase tracking-[0.1em] text-ink-faint transition-colors hover:border-bad hover:text-bad"
        >
          Sign out
        </button>
      </section>
    </main>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-[760px] px-5 py-10">
      <h1 className="font-serif text-4xl text-ink">Profile</h1>
      <div className="mt-6">{children}</div>
    </main>
  );
}

function Heading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="font-mono text-label uppercase tracking-[0.16em] text-ink-faint">{children}</h2>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <dd className="tabular font-mono text-2xl text-ink">{value}</dd>
      <dt className="font-mono text-label uppercase tracking-[0.12em] text-ink-faint">{label}</dt>
    </div>
  );
}

/** The first field error Clerk returns, which is the one a person can act on. */
function messageOf(cause: unknown): string {
  const errors = (cause as { errors?: { message?: string; longMessage?: string }[] })?.errors;
  return errors?.[0]?.longMessage ?? errors?.[0]?.message ?? "That did not save. Try again.";
}
