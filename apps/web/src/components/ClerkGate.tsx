"use client";

/**
 * Mounts Clerk when a *navigation* needs it, which the root layout cannot see.
 *
 * **The bug this exists for.** The root layout decides whether to mount `ClerkProvider` from the
 * request path and the session cookie (`lib/auth-scope`). That is right for a document request and
 * wrong for every soft navigation after it: the App Router preserves a shared layout across a
 * client-side navigation, so the layout does not re-render and its decision is whatever the
 * *previous* page made. A signed-out reader on `/` got no provider — correctly, there was nothing
 * to establish — and then clicked `sign in`, which is a soft navigation into a route that needs
 * one. `useSignIn` threw `useClerkSignal can only be used within the <ClerkProvider /> component`
 * and the root error boundary rendered "That did not load." over the site's own sign-in button.
 *
 * Reloading fixed it, which is exactly why it read as intermittent. Every test reached those pages
 * with `page.goto`, the one path that works.
 *
 * **Why the provider is not simply moved into a route group.** An `app/(identity)/layout.tsx` would
 * fix the way in and break the way out: a nested layout unmounts when you leave its segment, so
 * signing in and being pushed to `/play` would tear the provider down again and throw there
 * instead. The provider has to live above every route, which is where it already is. What was
 * wrong was not its position but *where the decision was evaluated* — on the server, once, in a
 * component that never re-renders. Here it is the same predicate, re-run on every navigation.
 *
 * **It is additive.** When the server already mounted the provider, this renders nothing but the
 * context. It can only add a provider where today there is none and the code throws.
 */

import { usePathname } from "next/navigation";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ComponentType,
  type ReactNode,
} from "react";

import { clerkEnabled } from "@/components/AuthProvider";
import { needsClerk } from "@/lib/auth-scope";

const ClerkMountedContext = createContext(false);

/**
 * Whether a `ClerkProvider` is above this component.
 *
 * A hook rather than the prop it replaced, because the answer now changes *during* a session —
 * the gate can mount a provider the server never did, and a prop threaded from the layout would
 * still be reporting what was true at first paint. Anything calling a Clerk hook must check this
 * first; the hooks throw rather than returning a signed-out answer.
 */
export function useClerkMounted(): boolean {
  return useContext(ClerkMountedContext);
}

/**
 * Clerk's own signed-out marker, read from the browser.
 *
 * The exact name, not a prefix match: Clerk also sets a suffixed `__client_uat_<instance>` beside
 * it, and `startsWith("__client_uat")` would match that one first and read the wrong instance's
 * value on a machine that has talked to two of them.
 */
function clientUat(): string | undefined {
  if (typeof document === "undefined") return undefined;
  const name = "__client_uat=";
  return document.cookie
    .split("; ")
    .find((entry) => entry.startsWith(name))
    ?.slice(name.length);
}

export function ClerkGate({
  /** Whether the root layout already wrapped this tree in `AuthProvider` for this request. */
  serverMounted,
  children,
}: {
  serverMounted: boolean;
  children: ReactNode;
}) {
  const pathname = usePathname();
  const [Provider, setProvider] = useState<ComponentType<{ children: ReactNode }> | null>(null);

  const wanted = !serverMounted && clerkEnabled && needsClerk(pathname, clientUat());

  useEffect(() => {
    if (!wanted || Provider) return;

    /**
     * Imported here rather than at the top of the file, and that is the whole reason this is a
     * separate module. A static import would put `@clerk/nextjs` in *this* component's chunk, and
     * this component is in the root layout — so the 87 KiB that `lib/auth-scope` exists to keep
     * off `/about` and `/leaderboard` would be downloaded on every route, by the very thing
     * written to avoid it. A signed-out reader who never visits an identity route still fetches
     * nothing.
     */
    let cancelled = false;
    void import("@/components/AuthProvider").then((module) => {
      if (!cancelled) setProvider(() => module.AuthProvider);
    });
    return () => {
      cancelled = true;
    };
  }, [wanted, Provider]);

  /* Latched: once mounted it is never unmounted, even when `wanted` goes false again. Navigating
     away from `/sign-in` after signing in would otherwise tear Clerk down at the exact moment the
     rest of the site started needing it — the same failure as the original bug, one step later. */
  const inner = (
    <ClerkMountedContext.Provider value={serverMounted || Provider !== null}>
      {children}
    </ClerkMountedContext.Provider>
  );

  if (serverMounted || !Provider) return inner;
  return <Provider>{inner}</Provider>;
}
