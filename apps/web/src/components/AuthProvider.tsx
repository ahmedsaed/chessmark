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
