import type { Metadata } from "next";
import { cookies } from "next/headers";
import Link from "next/link";

import { BuyCredit } from "@/components/BuyCredit";
import { getCreditAvailability, getCreditOptions } from "@/lib/api";
import { hasSessionCookie } from "@/lib/auth-scope";
import { paddleConfig } from "@/lib/paddle";
import { pageMetadata } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Credit",
  description: "Buy credit for games against paid models, charged per move at what the model cost.",
  path: "/credit",
});

/**
 * Buying credit (ADR-0055).
 *
 * Public, so what credit is and what it costs can be read before signing up — and so Paddle's
 * reviewer can see what is sold. Every figure comes from the API, which is the only place that
 * knows what an amount grants; this page shows its numbers and does no arithmetic of its own.
 *
 * Selling needs both halves configured: the API's Paddle settings and this build's Paddle token.
 * Either one missing still shows the amounts and their arithmetic — what credit costs is public
 * before it is on sale, and a payment processor's review looks for it — with "not on sale yet" where
 * Buy would be.
 */
export default async function CreditPage() {
  const signedIn = hasSessionCookie((await cookies()).get("__client_uat")?.value);
  const [options, availability] = await Promise.all([getCreditOptions(), getCreditAvailability()]);
  const onSale = options.selling && paddleConfig !== null;

  return (
    <main className="mx-auto w-full max-w-[880px] flex-1 px-5 py-12">
      <h1 className="font-serif text-4xl leading-tight text-ink">Credit</h1>
      <p className="mt-4 max-w-prose leading-relaxed text-ink-dim">
        Games against paid models are charged per move, at what the model cost. Games against free
        models cost nothing.
      </p>

      <section className="mt-10">
        {options.presets.length > 0 ? (
          <BuyCredit
            options={options}
            availability={availability}
            signedIn={signedIn}
            selling={onSale}
          />
        ) : (
          <p className="border border-line-soft bg-surface px-4 py-5 text-sm text-ink-dim">
            Buying credit is not open yet.
          </p>
        )}
      </section>

      <ul className="mt-10 flex max-w-prose list-disc flex-col gap-2 pl-5 text-sm leading-relaxed text-ink-dim marker:text-ink-faint">
        <li>Credit has no cash value. Credit unused for 12 months may expire.</li>
        <li>When it runs out, your paid games pause. They resume when you add more.</li>
        <li>
          Credit you have not spent can be refunded within 14 days of buying it. See the{" "}
          <Link href="/refunds" className="text-accent underline underline-offset-4">
            refund policy
          </Link>{" "}
          and the{" "}
          <Link href="/terms" className="text-accent underline underline-offset-4">
            terms
          </Link>
          .
        </li>
      </ul>
    </main>
  );
}
