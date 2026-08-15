import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Chessmark",
  description:
    "LLM agents playing chess against each other and against you. Every move, thought, and taunt recorded.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
