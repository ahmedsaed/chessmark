"use client";

/**
 * The packs, and Paddle's checkout for the one chosen (ADR-0055).
 *
 * **This never moves a balance.** It opens Paddle's overlay with the pack's price, the buyer's email
 * and their Clerk id; Paddle takes the payment, and its signed webhook to our API is what credits
 * the account. What the browser sends cannot change how much: the API maps the *price paid* to
 * credit itself (`core/credit_packs.py`).
 *
 * **So after a payment, this waits for the webhook** — asking the API about that one transaction
 * every two seconds, for up to a minute. That is not a loading costume over a slow read: the money
 * has moved and the credit is arriving by a different road, usually within seconds, and saying
 * "added" before it has would be a claim the header then contradicts.
 *
 * Paddle.js is loaded only here, only for a signed-in buyer, and only when selling is configured.
 */

import { useAuth, useUser } from "@clerk/nextjs";
import { initializePaddle, type Paddle, type PaddleEventData } from "@paddle/paddle-js";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { SUPPORT_EMAIL } from "@/components/LegalPage";
import { getPurchase, type CreditPack } from "@/lib/api";
import { announceSpend, formatBalance } from "@/lib/credit";
import { BUYER_KEY, paddleConfig } from "@/lib/paddle";

const POLL_MS = 2_000;
const POLL_FOR_MS = 60_000;

type Arrival =
  | { state: "idle" }
  | { state: "waiting"; credit: string }
  | { state: "credited"; credit: string; balance: string }
  | { state: "late"; credit: string }
  | { state: "unmatched" };

export function BuyCredit({
  packs,
  signedIn,
  selling,
}: {
  packs: CreditPack[];
  signedIn: boolean;
  /** False shows the packs and their arithmetic with nothing to buy: what credit costs is public
   *  before it is on sale. */
  selling: boolean;
}) {
  return (
    <ul className="grid gap-4 sm:grid-cols-3">
      {packs.map((pack) => (
        <li key={pack.price_usd} className="flex flex-col gap-5 border border-line bg-surface px-5 py-5">
          {/* A sum, top to bottom: what you pay, what comes out of it, what reaches your balance.
              Three different numbers side by side read as three prices; laid out as arithmetic
              they read as one purchase. */}
          <p className="tabular font-serif text-4xl text-ink">{dollars(pack.price_usd)}</p>
          <dl className="tabular flex flex-col gap-1.5 font-mono text-meta">
            <Line label="Payment processor" rule="5% + $0.50" amount={`− ${dollars(pack.processor_fee_usd)}`} />
            <Line label="Running Chessmark" rule="5%" amount={`− ${dollars(pack.upkeep_usd)}`} />
            <div className="mt-1.5 flex items-baseline justify-between gap-3 border-t border-line pt-2.5">
              <dt className="uppercase tracking-[0.12em] text-ink">Your credit</dt>
              <dd className="text-lg text-accent">{dollars(pack.credit_usd)}</dd>
            </div>
          </dl>
          <p className="-mt-2 text-xs text-ink-faint">Tax is added at checkout where it applies.</p>
          {!selling || pack.price_id === null ? (
            <button type="button" disabled className={BUY}>
              not on sale yet
            </button>
          ) : signedIn ? (
            <BuyButton pack={pack} priceId={pack.price_id} />
          ) : (
            <Link href="/sign-in?redirect=/credit" className={BUY}>
              sign in to buy
            </Link>
          )}
        </li>
      ))}
    </ul>
  );
}

function Line({ label, rule, amount }: { label: string; rule: string; amount: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-ink-faint">
      <dt>
        {label} ({rule})
      </dt>
      <dd className="text-ink-dim">{amount}</dd>
    </div>
  );
}

/* One Paddle per page, shared by the three buttons, and the arrival it reports on. Kept at module
   scope because `initializePaddle` refuses a second call. */
let paddlePromise: Promise<Paddle | undefined> | null = null;
const listeners = new Set<(event: PaddleEventData) => void>();

function loadPaddle(): Promise<Paddle | undefined> {
  if (!paddleConfig) return Promise.resolve(undefined);
  paddlePromise ??= initializePaddle({
    token: paddleConfig.token,
    environment: paddleConfig.environment,
    eventCallback: (event) => listeners.forEach((listener) => listener(event)),
  });
  return paddlePromise;
}

function BuyButton({ pack, priceId }: { pack: CreditPack; priceId: string }) {
  const { getToken, userId } = useAuth();
  const { user } = useUser();
  const [paddle, setPaddle] = useState<Paddle | undefined>();
  const [arrival, setArrival] = useState<Arrival>({ state: "idle" });
  const [error, setError] = useState<string | null>(null);
  const mine = useRef(false);

  useEffect(() => {
    let alive = true;
    loadPaddle().then(
      (loaded) => alive && setPaddle(loaded),
      () => alive && setError("The checkout did not load. Try reloading the page."),
    );
    return () => {
      alive = false;
    };
  }, []);

  const waitFor = useCallback(
    async (transactionId: string) => {
      setArrival({ state: "waiting", credit: pack.credit_usd });
      const deadline = Date.now() + POLL_FOR_MS;
      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        try {
          const purchase = await getPurchase(transactionId, await getToken());
          if (purchase?.status === "credited") {
            setArrival({ state: "credited", credit: purchase.credit_usd, balance: purchase.balance_usd });
            announceSpend(); // the header re-reads the balance
            return;
          }
          if (purchase?.status === "unmatched") {
            setArrival({ state: "unmatched" });
            return;
          }
        } catch {
          /* A failed check is not a failed purchase; the next one may answer. */
        }
      }
      setArrival({ state: "late", credit: pack.credit_usd });
    },
    [getToken, pack.credit_usd],
  );

  useEffect(() => {
    function onEvent(event: PaddleEventData) {
      if (!mine.current) return;
      if (event.name === "checkout.completed" && event.data?.transaction_id) {
        void waitFor(event.data.transaction_id);
      }
      if (event.name === "checkout.closed") mine.current = false;
    }
    listeners.add(onEvent);
    return () => {
      listeners.delete(onEvent);
    };
  }, [waitFor]);

  function open() {
    if (!paddle || !userId) return;
    setError(null);
    setArrival({ state: "idle" });
    mine.current = true;
    const email = user?.primaryEmailAddress?.emailAddress;
    paddle.Checkout.open({
      items: [{ priceId, quantity: 1 }],
      customData: { [BUYER_KEY]: userId },
      ...(email ? { customer: { email } } : {}),
      settings: { displayMode: "overlay", variant: "one-page", theme: "dark", allowLogout: !email },
    });
  }

  return (
    <div className="flex flex-col gap-2">
      <button type="button" onClick={open} disabled={!paddle || !userId} className={BUY}>
        buy
      </button>
      <ArrivalNote arrival={arrival} />
      {error && <p className="font-mono text-meta text-bad">{error}</p>}
    </div>
  );
}

function ArrivalNote({ arrival }: { arrival: Arrival }) {
  switch (arrival.state) {
    case "idle":
      return null;
    case "waiting":
      return <Note tone="text-ink-dim">Paid. Adding {formatBalance(arrival.credit)} to your credit…</Note>;
    case "credited":
      return (
        <Note tone="text-accent">
          Added {formatBalance(arrival.credit)}. Your credit is {formatBalance(arrival.balance)}.
        </Note>
      );
    case "late":
      return (
        <Note tone="text-bad">
          Paid, and the credit has not arrived yet. It can take a few minutes; if it is not there
          within the hour, write to {SUPPORT_EMAIL}.
        </Note>
      );
    case "unmatched":
      return (
        <Note tone="text-bad">
          Paid, but the purchase could not be matched to your account. Write to {SUPPORT_EMAIL} and
          it will be added.
        </Note>
      );
  }
}

function Note({ tone, children }: { tone: string; children: React.ReactNode }) {
  return (
    <p role="status" className={`font-mono text-meta ${tone}`}>
      {children}
    </p>
  );
}

/** "$5.00" from "5". The API's own figures, only formatted: the checkout shows what the buyer pays
 *  with their tax, and this page does no arithmetic. */
function dollars(usd: string): string {
  return `$${Number(usd).toFixed(2)}`;
}

const BUY =
  "border border-accent-deep bg-accent px-3 py-2 text-center font-mono text-meta uppercase tracking-[0.1em] text-on-accent transition-colors hover:bg-accent-dim disabled:opacity-40";
