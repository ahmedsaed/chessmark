import type { Metadata } from "next";

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
