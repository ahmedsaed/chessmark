import type { Metadata, Viewport } from "next";

import { cookies, headers } from "next/headers";

import { AuthProvider } from "@/components/AuthProvider";
import { ClerkGate } from "@/components/ClerkGate";
import { needsClerk } from "@/lib/auth-scope";
import { PATHNAME_HEADER } from "@/proxy";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteHeader } from "@/components/SiteHeader";
import { siteDescription, siteName, siteTagline, siteUrl } from "@/lib/site";
import "./globals.css";

export const metadata: Metadata = {
  /* Without `metadataBase` the OpenGraph image resolves relative and social cards come back
     blank. Individual games have had a card since Phase 8; the site itself never did. */
  metadataBase: new URL(siteUrl),
  title: {
    default: `${siteName} — ${siteTagline}`,
    /* Pages set a bare title; the wordmark is appended here so no page repeats it. */
    template: `%s — ${siteName}`,
  },
  description: siteDescription,
  applicationName: siteName,
  openGraph: {
    type: "website",
    siteName,
    title: `${siteName} — ${siteTagline}`,
    description: siteDescription,
    url: siteUrl,
  },
  twitter: { card: "summary_large_image" },
  /* No `alternates.canonical` here on purpose. Metadata keys are inherited wholesale by any
     segment that does not set them, so a canonical on the root layout makes every page in the
     site declare itself a duplicate of `/`. Each page sets its own; `canonicalFor` builds it. */
};

/* Split from `metadata` because Next.js 16 errors on `themeColor` inside it.
   `colorScheme` tells the browser to render form controls and scrollbars dark; `globals.css`
   already sets it on `html`, and this puts it in the document head where the browser reads it
   before the stylesheet arrives — which is the difference between a white flash and none. */
export const viewport: Viewport = {
  themeColor: "#16130e",
  colorScheme: "dark",
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  /**
   * **Clerk is mounted only when this request needs it.**
   *
   * A signed-out reader on `/leaderboard` has no identity to establish and nothing to show for
   * one, so they download no Clerk at all — 87 KiB that used to load on every route. `needsClerk`
   * answers from Clerk's own `__client_uat` cookie and the path, neither of which requires Clerk
   * to read, and the header draws the signed-out bar from the same answer rather than from a hook.
   *
   * Both come from request-scoped APIs, which makes this layout dynamic — it already was, every
   * route in this app is (FRONTEND.md).
   */
  const [cookieStore, headerList] = await Promise.all([cookies(), headers()]);
  const pathname = headerList.get(PATHNAME_HEADER) ?? "/";
  const mountClerk = needsClerk(pathname, cookieStore.get("__client_uat")?.value);

  /* `AuthProvider` sits **inside** `<body>`, not around `<html>`. Next.js 16 with cache
     components treats a provider wrapping `<html>` as uncached data accessed outside a
     `<Suspense>` boundary, which is an error rather than a warning. */
  /**
   * **`mountClerk` is only ever right for *this* request, and this layout does not re-render.**
   *
   * A soft navigation preserves a shared layout, so a reader who arrives on `/` signed out and
   * then clicks `sign in` reaches a route that needs Clerk with the decision `/` made. `ClerkGate`
   * re-runs the same predicate on every navigation and mounts the provider if this one did not.
   * It is the whole fix for "sign in sometimes says *That did not load.*"; the server decision
   * stays because it is what gives a direct hit on `/sign-in` its provider in the first payload,
   * with no client round trip and nothing to remount.
   */
  const shell = (
    <ClerkGate serverMounted={mountClerk}>
      <SiteHeader />
      {children}
      <SiteFooter />
    </ClerkGate>
  );

  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        {mountClerk ? <AuthProvider>{shell}</AuthProvider> : shell}
      </body>
    </html>
  );
}
