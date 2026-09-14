import type { Metadata, Viewport } from "next";

import { AuthProvider } from "@/components/AuthProvider";
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

export default function RootLayout({ children }: LayoutProps<"/">) {
  /* `AuthProvider` sits **inside** `<body>`, not around `<html>`. Next.js 16 with cache
     components treats a provider wrapping `<html>` as uncached data accessed outside a
     `<Suspense>` boundary, which is an error rather than a warning. */
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <AuthProvider>
          <SiteHeader />
          {children}
          <SiteFooter />
        </AuthProvider>
      </body>
    </html>
  );
}
