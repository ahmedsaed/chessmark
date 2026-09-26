"use client";

/**
 * The account screen, in the site's own type and colour.
 *
 * Built on `useUser` and `useClerk` rather than Clerk's `<UserProfile />` for the reason the whole
 * of this change exists: one prebuilt component anywhere puts 285 KiB of `@clerk/ui` on every
 * route. `useUser` and `useClerk` are hooks, not components, and cost nothing beyond the core.
 *
 * **What it deliberately does not do.** Clerk's own profile manages emails, passwords, connected
 * accounts, sessions and MFA. This manages a display name, and shows what you have played and what
 * it cost — because those are the things this site knows and Clerk does not. Re-implementing
 * account recovery is how people get locked out, so anything else stays with the provider that is
 * good at it: a person who needs to change their email does it wherever they signed in.
 *
 * The record and the game list are the same shapes a model page uses, over the same `GameCard`.
 * A person holding a seat is a player here, and the page that describes one should not be a
 * different kind of page depending on whether it is a person or a model.
 *
 * The balance comes from *our* API, not from Clerk — credit is a Chessmark concept (ADR-0052),
 * granted by an administrator and spent at what each model turn actually cost.
 */

import { useAuth, useClerk, useUser } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { clerkEnabled } from "@/components/AuthProvider";
import { GameCard } from "@/components/GameCard";
import { listMyGames } from "@/lib/api";
import { orderMyGames, recordOf } from "@/lib/mine";
import type { Me, MyGameSummary } from "@/lib/types";
import { formatBalance } from "@/lib/credit";

/** The server's ceiling on `/games/mine`. This page is a history, so it asks for all of it. */
const EVERY_GAME = 200;

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
  /* `null` is *not loaded or failed*, `[]` is *loaded and you have never played*. They render
     differently on purpose: an empty history invites you to start a game, and a failed read must
     not pretend to be one. The same distinction `reportFailure` exists for on the server. */
  const [games, setGames] = useState<MyGameSummary[] | null>(null);
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

      /* A second request rather than one, and not a new endpoint. `/games/mine` already returns
         every field the record needs, so W/D/L is arithmetic over a list the page was going to
         fetch anyway — an endpoint that returned the same numbers would be a second place for them
         to be computed, and the two would disagree the first time either changed. */
      try {
        const mine = await listMyGames(await getToken(), EVERY_GAME);
        if (!cancelled) setGames(mine);
      } catch {
        // Left as `null`, which renders as "could not be loaded" rather than as "none".
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [isSignedIn, getToken, apiUrl]);

  /* `firstName` first, and it is the only field actually written — see `save`. `username` stays
     as a fallback for a reader whose account already carries one, because Google sign-in can put
     one there even on an instance where the attribute is not editable. */
  const name = draft ?? user?.firstName ?? user?.username ?? "";

  const save = useCallback(async () => {
    if (!user) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      /* **`firstName`, not `username`.** `username` is an attribute a Clerk instance enables or
         does not, and ours does not — every save came back *"username is not a valid parameter
         for this request"*, so the display name could never be set at all. `first_name` is
         enabled and optional, and it is also what the API reads first when it resolves a person's
         name (`core/clerk.py::_display_name`), so what is typed here is what a game shows. */
      await user.update({ firstName: name.trim() });
      setSaved(true);
    } catch (cause) {
      /* Clerk's field errors are structured; the first message is the one a person can act on
         ("must be at most 256 characters"), and the rest repeat it in other words. */
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
            autoComplete="given-name"
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
        <Heading>Credit</Heading>
        <p className="mt-2 text-sm text-ink-dim">
          Credit is dollars, spent as your games play: each model turn is charged what it actually
          cost, and a free model costs nothing. When it runs out, a game pauses where it is and
          resumes once more is added. Credit is granted by an administrator and does not refill.
        </p>

        <dl className="mt-5 flex flex-wrap gap-10">
          <Stat label="credit" value={me ? formatBalance(me.balance_usd) : "—"} />
          <Stat label="games today" value={me ? String(me.games_started_today) : "—"} />
          <Stat
            label="spent today"
            value={me ? `$${Number(me.usd_spent_today).toFixed(4)}` : "—"}
          />
        </dl>
      </section>

      <Played games={games} />

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

/**
 * Your record, and every game behind it.
 *
 * **The record counts decided games and says so**, which is the distinction the model page had to
 * learn the hard way: it printed two different W/D/L figures over two different sets of games and
 * named neither, so which one a reader saw depended on how they arrived. A game the harness
 * stopped — a budget, a ply cap, a provider we could not reach — is not a draw and not a loss
 * (invariant 11), so it is counted apart rather than folded into either.
 */
function Played({ games }: { games: MyGameSummary[] | null }) {
  if (games === null) {
    return (
      <section className="mt-12">
        <Heading>Games</Heading>
        <p className="mt-3 border border-bad-deep bg-surface px-4 py-3 text-sm text-bad">
          Your games could not be loaded. This is a failed request, not an empty history — reload
          to try again.
        </p>
      </section>
    );
  }

  if (games.length === 0) {
    return (
      <section className="mt-12">
        <Heading>Games</Heading>
        <p className="mt-3 text-sm text-ink-dim">
          You have not played yet.{" "}
          <Link className="text-accent underline underline-offset-4" href="/play">
            Sit down against a model
          </Link>{" "}
          — watching needs no account, and a free model costs nothing to play.
        </p>
      </section>
    );
  }

  const record = recordOf(games);
  const ordered = orderMyGames(games);

  return (
    <>
      <section className="mt-12">
        {/* Named just "Record". It said *decided games only* over a panel whose first cell counts
            every game, which is the same contradiction the model page had to remove when it
            printed two W/D/L figures and named neither. */}
        <Heading>Record</Heading>
        <p className="mt-2 text-sm text-ink-dim">
          Games that reached a result. A game we stopped ourselves — a budget, a ply cap, a
          provider that would not answer — is counted apart: it ended the game, and it says nothing
          about how either side played.
        </p>

        <dl className="mt-4 grid grid-cols-2 gap-px border border-line-soft bg-line-soft sm:grid-cols-4">
          <Fact label="Played" value={String(games.length)} />
          <Fact
            label="W / D / L"
            value={`${record.wins} / ${record.draws} / ${record.losses}`}
            note={`of ${record.decided} decided`}
          />
          <Fact
            label="In progress"
            value={String(record.unfinished)}
            note={record.unfinished > 0 ? "running or paused" : undefined}
          />
          <Fact
            label="No result"
            value={String(record.undecided)}
            note={record.undecided > 0 ? "stopped by the harness" : undefined}
          />
        </dl>
      </section>

      <section className="mt-12">
        <Heading>Your games</Heading>
        <p className="mt-2 text-sm text-ink-dim">
          Waiting on you first, then what is still running, then what is finished.
        </p>

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {ordered.map((game) => (
            <GameCard
              key={game.id}
              game={game}
              seat={game.your_colour}
              yourTurn={game.your_turn}
            />
          ))}
        </div>
      </section>
    </>
  );
}

function Fact({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="bg-surface px-3 py-2.5">
      <dt className="font-mono text-label uppercase tracking-[0.14em] text-ink-faint">{label}</dt>
      <dd className="tabular mt-1 font-mono text-sm text-ink">{value}</dd>
      {/* A second `<dd>`, not a `<p>`: a `<dl>` group may hold only terms and descriptions, and
          the `<p>` made the list invalid for a screen reader, which announces a `<dl>` as pairs. */}
      {note && <dd className="tabular mt-0.5 font-mono text-label text-ink-faint">{note}</dd>}
    </div>
  );
}

/** The first field error Clerk returns, which is the one a person can act on. */
function messageOf(cause: unknown): string {
  const errors = (cause as { errors?: { message?: string; longMessage?: string }[] })?.errors;
  return errors?.[0]?.longMessage ?? errors?.[0]?.message ?? "That did not save. Try again.";
}
