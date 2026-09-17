"use client";

/**
 * Where Google sends the browser back to.
 *
 * `AuthenticateWithRedirectCallback` is a **control** component, not a UI one — it renders nothing
 * and only finishes the handshake — so it is exactly what `prefetchUI={false}` is documented to
 * support, and it costs no part of the `@clerk/ui` bundle.
 *
 * It needs to exist as a route: `authenticateWithRedirect` names it as `redirectUrl`, and without
 * it the provider returns to a 404 with the session half-created.
 */

import { AuthenticateWithRedirectCallback } from "@clerk/nextjs";

export default function SsoCallbackPage() {
  return (
    <main className="mx-auto flex w-full max-w-[420px] px-5 py-16">
      <p className="font-mono text-meta uppercase tracking-[0.16em] text-ink-faint">Signing you in…</p>
      <AuthenticateWithRedirectCallback />
    </main>
  );
}
