/**
 * Clerk, made optional.
 *
 * Watching games needs no account (AUTH-02), and neither does running the project locally. Without
 * a publishable key `ClerkProvider` throws on render, which would mean a clone of this repo could
 * not open the lobby until someone signed up for Clerk — a bad trade for a feature that only
 * matters when you want to *start* a game.
 *
 * So the provider is mounted only when a key is present. Everything auth-related degrades to
 * signed-out, which is exactly what it is.
 */

import { ClerkProvider } from "@clerk/nextjs";

export const clerkEnabled = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  if (!clerkEnabled) return <>{children}</>;

  return (
    <ClerkProvider
      /**
       * **No prebuilt Clerk UI on this site, so none of it is fetched.**
       *
       * `prefetchUI` is documented as `false` — *"Skip prefetching the UI (for custom UIs using
       * Control Components)"* — and that parenthesis is load-bearing: it throws *"Clerk was not
       * loaded with Ui components"* with no lazy fallback if any prebuilt component renders. It is
       * not a flag that can be flipped on a site using `<SignIn />`; it is the reward for owning
       * those screens, which `AuthForm` and `ProfileView` now do.
       *
       * Worth **285 KiB on every route** — `@clerk/ui` was loading on `/about` and `/leaderboard`,
       * where nobody signs in. Clerk drops from 372 KiB to 87 KiB and the page from 619 to 334.
       *
       * What still renders from Clerk is control components only: `Show` and
       * `AuthenticateWithRedirectCallback`. Both are explicitly supported here. Adding a prebuilt
       * component anywhere re-breaks this, silently and site-wide — which is why the browser suite
       * asserts the UI bundle is never requested.
       */
      prefetchUI={false}
      appearance={{
        variables: {
          colorBackground: "#16130f",
          colorPrimary: "#d99a2b",
          colorForeground: "#e8e2d9",
          borderRadius: "2px",
        },
      }}
    >
      {children}
    </ClerkProvider>
  );
}
