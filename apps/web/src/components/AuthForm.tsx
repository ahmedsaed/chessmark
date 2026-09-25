"use client";

/**
 * Sign in and sign up, in the site's own type and colour.
 *
 * **One component for both, because they are one flow.** Google is the same button either way, and
 * the email route differs only in which Clerk object it drives — `signIn` or `signUp`. Two files
 * would have been two copies of the code-entry step, which is the part with the states.
 *
 * Replaces `<SignIn />` and `<SignUp />`. Those are prebuilt components, and one prebuilt component
 * anywhere loads `@clerk/ui` on **every** route — 285 KiB on `/about` as much as here. That is the
 * whole reason this exists; the theme control is the part we also wanted.
 *
 * **Scope is deliberately two strategies: Google, and an emailed code.** Those are what the
 * instance has enabled. Signing up also sets a password, because the instance requires one — the
 * form went to production without that field and every email sign-up died on
 * `missing_requirements` with the account half-created. It is not asked for again at sign-in: the
 * emailed code is the first factor here, and the password exists to satisfy the instance and to be
 * there if password sign-in is ever turned on. Every other branch Clerk supports — password, MFA, `missing_requirements`,
 * session tasks — is either unreachable here or surfaces as an error a person can read, rather than
 * being half-implemented. Hand-rolled auth fails by locking somebody out, so the flows that are not
 * covered must say so rather than appear to work.
 */

import { useSignIn, useSignUp } from "@clerk/nextjs";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";

/**
 * The instance's minimum password length.
 *
 * Duplicated from the Clerk dashboard deliberately: the value is a dashboard setting and this is a
 * hint, not the enforcement. Clerk rejects a short password whatever this says — the cost of the
 * two drifting is a message that is wrong by a few characters, not a weak password getting in.
 */
const PASSWORD_MIN_LENGTH = 15;

type Mode = "sign-in" | "sign-up";
type Step = "identify" | "code";

export function AuthForm({ mode }: { mode: Mode }) {
  /* **Core 3 hooks.** `useSignIn` returns `{ signIn, errors, fetchStatus }` — no `isLoaded`, no
     `setActive`, and the resource carries step methods that map to the flow rather than the
     `create`/`prepare`/`attempt` triple of Core 2. Written against the installed `.d.ts` rather
     than from memory, because the Core 2 shape typechecks in one's head and in nothing else. */
  const { signIn, fetchStatus: signInStatus } = useSignIn();
  const { signUp, fetchStatus: signUpStatus } = useSignUp();
  const router = useRouter();
  const params = useSearchParams();

  const [step, setStep] = useState<Step>("identify");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* Where to land afterwards. `?redirect=` is set by the links that send people here — the modal
     this replaced returned you to the page you were on, and a page has to be told. Only a relative
     path is honoured: an absolute one is an open redirect, which is somebody else's phishing page
     wearing our sign-in. */
  const redirect = safeRedirect(params.get("redirect"));
  const working = (mode === "sign-in" ? signInStatus : signUpStatus) === "fetching" || busy;

  const withGoogle = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      const strategy = "oauth_google" as const;
      /* Two URLs, and they are not the same one. `redirectCallbackUrl` is where *Google* returns
         the browser — our `/sso-callback`, which mounts the control component that finishes the
         handshake. `redirectUrl` is where that lands afterwards. */
      const sso = { strategy, redirectCallbackUrl: "/sso-callback", redirectUrl: redirect };
      const result = mode === "sign-in" ? await signIn?.sso(sso) : await signUp?.sso(sso);
      if (result?.error) throw result.error;
    } catch (cause) {
      setError(messageOf(cause));
      setBusy(false);
    }
  }, [mode, signIn, signUp, redirect]);

  const sendCode = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      if (mode === "sign-in") {
        if (!signIn) return;
        const created = await signIn.create({ identifier: email.trim() });
        if (created.error) throw created.error;
        const sent = await signIn.emailCode.sendCode();
        if (sent.error) throw sent.error;
      } else {
        if (!signUp) return;
        const created = await signUp.create({ emailAddress: email.trim(), password });
        if (created.error) throw created.error;
        const sent = await signUp.verifications.sendEmailCode();
        if (sent.error) throw sent.error;
      }
      setStep("code");
    } catch (cause) {
      setError(messageOf(cause));
    } finally {
      setBusy(false);
    }
  }, [mode, email, password, signIn, signUp]);

  const verify = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      if (mode === "sign-in") {
        if (!signIn) return;
        const verified = await signIn.emailCode.verifyCode({ code: code.trim() });
        if (verified.error) throw verified.error;
        if (signIn.status !== "complete") {
          /* Anything that is not `complete` is a branch this form does not implement — a second
             factor, a missing field. Saying so beats a spinner that never resolves, and beats
             pretending a half-finished sign-in worked. */
          throw new Error(`This sign-in needs a step we do not support here (${signIn.status}).`);
        }
        const done = await signIn.finalize({ navigate: () => router.push(redirect) });
        if (done.error) throw done.error;
      } else {
        if (!signUp) return;
        const verified = await signUp.verifications.verifyEmailCode({ code: code.trim() });
        if (verified.error) throw verified.error;
        if (signUp.status !== "complete") {
          throw new Error(`This sign-up needs a step we do not support here (${signUp.status}).`);
        }
        const done = await signUp.finalize({ navigate: () => router.push(redirect) });
        if (done.error) throw done.error;
      }
    } catch (cause) {
      setError(messageOf(cause));
    } finally {
      setBusy(false);
    }
  }, [mode, code, signIn, signUp, router, redirect]);

  const heading = mode === "sign-in" ? "Sign in" : "Create an account";

  return (
    <main className="mx-auto flex w-full max-w-[420px] flex-col px-5 py-16">
      <h1 className="font-serif text-4xl text-ink">{heading}</h1>
      <p className="mt-2 text-sm text-ink-dim">
        Watching needs no account. Starting a game does, because every turn calls a provider.
      </p>

      <form
        className="mt-8 flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (step === "identify") void sendCode();
          else void verify();
        }}
      >
        <button
          type="button"
          onClick={withGoogle}
          disabled={working}
          className="flex items-center justify-center gap-2 border border-line bg-surface px-4 py-2.5 font-mono text-data uppercase tracking-[0.12em] text-ink transition-colors hover:border-accent-dim disabled:opacity-40"
        >
          Continue with Google
        </button>

        <div className="flex items-center gap-3 py-1">
          <span className="h-px flex-1 bg-line-soft" aria-hidden />
          <span className="font-mono text-label uppercase tracking-[0.16em] text-ink-faint">or</span>
          <span className="h-px flex-1 bg-line-soft" aria-hidden />
        </div>

        {step === "identify" ? (
          <>
            <label className="font-mono text-label uppercase tracking-[0.16em] text-ink-faint" htmlFor="email">
              Email address
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus-visible:border-accent"
            />

            {mode === "sign-up" && (
              <>
                <label
                  className="font-mono text-label uppercase tracking-[0.16em] text-ink-faint"
                  htmlFor="password"
                >
                  Password
                </label>
                <input
                  id="password"
                  type="password"
                  required
                  /* The instance's floor. Stated in the hint below and enforced here so the
                     browser says so before a round trip does; Clerk still checks it, and its
                     message wins when the two disagree. */
                  minLength={PASSWORD_MIN_LENGTH}
                  autoComplete="new-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="border border-line bg-surface px-3 py-2 font-mono text-sm text-ink outline-none focus-visible:border-accent"
                  aria-describedby="password-hint"
                />
                <p id="password-hint" className="font-mono text-meta text-ink-faint">
                  At least {PASSWORD_MIN_LENGTH} characters. You sign in with an emailed code, so
                  this is only ever needed to create the account.
                </p>
              </>
            )}
          </>
        ) : (
          <>
            <label className="font-mono text-label uppercase tracking-[0.16em] text-ink-faint" htmlFor="code">
              Code sent to {email}
            </label>
            <input
              id="code"
              /* `inputMode` and `one-time-code` are what make a phone offer the code from the
                 message rather than making somebody memorise six digits. */
              inputMode="numeric"
              autoComplete="one-time-code"
              required
              value={code}
              onChange={(event) => setCode(event.target.value)}
              className="tabular border border-line bg-surface px-3 py-2 font-mono text-lg tracking-[0.3em] text-ink outline-none focus-visible:border-accent"
            />
          </>
        )}

        <button
          type="submit"
          disabled={working}
          className="border border-accent-deep bg-accent px-4 py-2.5 font-mono text-data uppercase tracking-[0.12em] text-on-accent transition-colors hover:bg-accent-dim disabled:opacity-40"
        >
          {busy ? "Working…" : step === "identify" ? "Continue" : "Verify"}
        </button>

        {error && (
          <p role="alert" className="font-mono text-meta text-bad">
            {error}
          </p>
        )}
      </form>

      {/* Clerk's own bot protection mounts here when it is enabled on the instance. Without the
          element the widget has nowhere to render and sign-up fails with a captcha error. */}
      <div id="clerk-captcha" />

      <p className="mt-8 font-mono text-meta text-ink-faint">
        {mode === "sign-in" ? (
          <>
            No account? <Link className="text-accent underline underline-offset-4" href="/sign-up">Create one</Link>
          </>
        ) : (
          <>
            Already have one? <Link className="text-accent underline underline-offset-4" href="/sign-in">Sign in</Link>
          </>
        )}
      </p>
    </main>
  );
}

/**
 * A redirect target we are willing to honour.
 *
 * Only a path, never a URL. `?redirect=https://elsewhere.example` on our own sign-in page is an
 * open redirect — a phishing page reached through a link that genuinely starts at our domain — and
 * it costs one line to refuse. `//host` is a protocol-relative URL and is refused for the same
 * reason.
 */
function safeRedirect(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/play";
  return value;
}

/** The first field error Clerk returns, which is the one a person can act on. */
function messageOf(cause: unknown): string {
  const errors = (cause as { errors?: { message?: string; longMessage?: string }[] })?.errors;
  if (errors?.length) return errors[0].longMessage ?? errors[0].message ?? "That did not work.";
  return cause instanceof Error ? cause.message : "That did not work. Try again.";
}
