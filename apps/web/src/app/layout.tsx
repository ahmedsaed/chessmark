import type { Metadata } from "next";

import { AuthProvider } from "@/components/AuthProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Chessmark",
  description:
    "LLM agents playing chess against each other and against you. Every move, thought, and taunt recorded.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  /* `AuthProvider` sits **inside** `<body>`, not around `<html>`. Next.js 16 with cache
     components treats a provider wrapping `<html>` as uncached data accessed outside a
     `<Suspense>` boundary, which is an error rather than a warning. */
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
