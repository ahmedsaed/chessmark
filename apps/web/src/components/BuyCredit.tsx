"use client";

/**
 * Choosing an amount of credit, and Paddle's checkout for it (ADR-0055, ADR-0056).
 *
 * **The amount is the buyer's**: one of three quick picks, or any whole number of dollars in the
 * range. Every figure in the breakdown is the API's — the presets arrive quoted with the page, and a
 * typed amount is quoted by `GET /credit/quote` — so the page lays out a sum it never computes.
 *
 * **This never moves a balance.** Buy asks the API to reserve the credit and create a Paddle
 * transaction at that price; the API refuses, with the largest amount it can sell, when OpenRouter's
 * balance cannot cover it. Paddle's overlay then opens on that transaction, and Paddle's signed
 * webhook to our API is what credits the account — for what the reservation says, not for anything
 * this page sent.
 *
 * **After a payment it waits for the webhook**, asking about that one transaction every two
 * seconds for up to a minute. The money has moved and the credit arrives by a different road,
 * usually within seconds; saying "added" before it has would be contradicted by the header.
 *
 * Paddle.js is loaded only here, only for a signed-in buyer, and only when selling is configured.
 */

import { useAuth, useUser } from "@clerk/nextjs";
import { initializePaddle, type Paddle, type PaddleEventData } from "@paddle/paddle-js";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { SUPPORT_EMAIL } from "@/components/LegalPage";
import {
  ApiError,
  getCreditQuote,
  getPurchase,
  startCheckout,
  type CreditAvailability,
  type CreditOptions,
  type CreditQuote,
} from "@/lib/api";
import { announceSpend, formatBalance } from "@/lib/credit";
import { paddleConfig } from "@/lib/paddle";

const POLL_MS = 2_000;
const POLL_FOR_MS = 60_000;
/** How long Paddle's own "payment successful" screen shows before the checkout closes itself. */
const CLOSE_AFTER_MS = 1_500;
/** A typed amount is quoted once the typing pauses, not on every keystroke. */
const QUOTE_AFTER_MS = 300;

type Arrival =
  | { state: "idle" }
  | { state: "waiting"; credit: string }
  | { state: "credited"; credit: string; balance: string }
  | { state: "late" }
  | { state: "unmatched" };

export function BuyCredit({
  options,
  availability,
  signedIn,
  selling,
}: {
  options: CreditOptions;
  /** What can be bought right now, from the API's stored OpenRouter balance (ADR-0056). A hint:
   *  Buy re-checks against a fresh read. */
  availability: CreditAvailability;
  signedIn: boolean;
  /** False shows the amounts and their arithmetic with nothing to buy: what credit costs is public
   *  before it is on sale. */
  selling: boolean;
}) {
  const first = options.presets[0];
  const [amount, setAmount] = useState(first ? whole(first.price_usd) : whole(options.min_usd));
  /* Only a typed amount's quote is fetched and held; it is tagged with the amount it is for, so a
     slow answer for an earlier keystroke is never shown against a later one. */
  const [fetched, setFetched] = useState<{
    amount: string;
    quote?: CreditQuote;
    error?: string;
  } | null>(null);

  const preset = options.presets.find((p) => whole(p.price_usd) === amount);
  /* The most that can be bought now: the range's top, or less while the headroom is short. */
  const largest =
    availability.state === "available" && availability.largest_usd !== null
      ? Math.min(Number(availability.largest_usd), Number(options.max_usd))
      : Number(options.max_usd);
  const soldOut = selling && availability.state === "sold_out";
  const paused = selling && availability.state === "unknown";

  const wholeNumber = /^\d+$/.test(amount);
  /* The range is checked here too, so an amount the API would refuse is not sent to be refused.
     A comparison of the API's own bounds, not arithmetic on money. */
  const inRange =
    wholeNumber &&
    Number(amount) >= Number(options.min_usd) &&
    Number(amount) <= Number(options.max_usd);
  const typed = !preset && inRange;
  const answer = fetched?.amount === amount ? fetched : null;
  const quote = preset ?? answer?.quote ?? null;
  const tooMuch = selling && wholeNumber && Number(amount) > largest && largest < Number(options.max_usd);
  const problem = tooMuch
    ? `Only up to $${largest} can be bought right now.`
    : preset || !amount
    ? null
    : !wholeNumber
      ? "Choose a whole number of dollars."
      : !inRange
        ? `Choose between $${whole(options.min_usd)} and $${whole(options.max_usd)}.`
        : (answer?.error ?? null);

  /* Quote what was typed, once the typing pauses. A preset's quote is already in hand. */
  useEffect(() => {
    if (!typed) return;
    let current = true;
    const timer = setTimeout(() => {
      getCreditQuote(amount).then(
        (quoted) => current && setFetched({ amount, quote: quoted }),
        (error: unknown) =>
          current &&
          setFetched({
            amount,
            error: error instanceof ApiError ? error.message : "That amount could not be quoted.",
          }),
      );
    }, QUOTE_AFTER_MS);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [amount, typed]);

  return (
    <div className="grid gap-6 md:grid-cols-2 md:gap-10">
      <div className="flex flex-col gap-4">
        <p className="font-mono text-meta uppercase tracking-[0.14em] text-ink-faint">Amount</p>
        <div className="flex flex-wrap gap-2">
          {options.presets.map((preset) => {
            const value = whole(preset.price_usd);
            const chosen = value === amount;
            return (
              <button
                key={value}
                type="button"
                aria-pressed={chosen}
                disabled={selling && Number(value) > largest}
                onClick={() => setAmount(value)}
                className={`tabular border px-4 py-2 font-mono text-data transition-colors disabled:opacity-40 ${
                  chosen
                    ? "border-accent bg-accent text-on-accent"
                    : "border-line bg-surface text-ink-dim hover:border-accent-dim hover:text-ink"
                }`}
              >
                ${value}
              </button>
            );
          })}
        </div>
        <label className="flex flex-col gap-1.5">
          <span className="text-sm text-ink-dim">
            Or any amount from ${whole(options.min_usd)} to ${selling ? largest : whole(options.max_usd)}
            {selling && largest < Number(options.max_usd) && !soldOut && " right now"}
          </span>
          {/* The input is the whole field, "$" drawn inside it, so the site's one focus ring goes
              round all of it — a ring on an inner input cut through the "$" beside it. */}
          <span className="relative block">
            <span
              aria-hidden
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 font-mono text-ink-faint"
            >
              $
            </span>
            <input
              type="text"
              inputMode="numeric"
              autoComplete="off"
              value={amount}
              onChange={(event) => setAmount(event.target.value.trim())}
              className="tabular w-full border border-line bg-surface py-2 pl-7 pr-3 font-mono text-ink"
              aria-describedby="amount-problem"
            />
          </span>
          <span id="amount-problem" role="status" className="min-h-[1.25rem] text-xs text-bad">
            {problem}
          </span>
        </label>
      </div>

      <div className="flex flex-col gap-5 border border-line bg-surface px-5 py-5">
        {/* A sum, top to bottom: what you pay, what comes out of it, what reaches your balance. */}
        <p className="tabular font-serif text-4xl text-ink">
          {quote ? dollars(quote.price_usd) : "—"}
        </p>
        <dl className="tabular flex flex-col gap-1.5 font-mono text-meta">
          <Line label="Payment processor" rule="5% + $0.50" amount={quote?.processor_fee_usd} />
          <Line label="Running Chessmark" rule="5%" amount={quote?.upkeep_usd} />
          <Line label="AI provider fee" rule="5.5%" amount={quote?.provider_fee_usd} />
          <div className="mt-1.5 flex items-baseline justify-between gap-3 border-t border-line pt-2.5">
            <dt className="uppercase tracking-[0.12em] text-ink">Your credit</dt>
            <dd className="text-lg text-accent">{quote ? dollars(quote.credit_usd) : "—"}</dd>
          </div>
        </dl>
        <p className="-mt-2 text-xs text-ink-faint">Tax is added at checkout where it applies.</p>
        {!selling || soldOut || paused ? (
          <div className="flex flex-col gap-2">
            <button type="button" disabled className={BUY}>
              {!selling ? "not on sale yet" : soldOut ? "sold out for now" : "paused for a moment"}
            </button>
            {soldOut && (
              <p className="text-xs text-ink-faint">
                Credit is sold only while our AI provider balance can cover it. More will be on
                sale soon.
              </p>
            )}
            {paused && (
              <p className="text-xs text-ink-faint">Buying is paused for a moment. Try again shortly.</p>
            )}
          </div>
        ) : signedIn ? (
          <Checkout amount={quote && !tooMuch ? whole(quote.price_usd) : null} />
        ) : (
          <Link href="/sign-in?redirect=/credit" className={BUY}>
            sign in to buy
          </Link>
        )}
      </div>
    </div>
  );
}

function Line({ label, rule, amount }: { label: string; rule: string; amount?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-ink-faint">
      <dt>
        {label} ({rule})
      </dt>
      <dd className="text-ink-dim">{amount ? `− ${dollars(amount)}` : "—"}</dd>
    </div>
  );
}

/* One Paddle per page, and the events it reports. Kept at module scope because `initializePaddle`
   refuses a second call. */
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

function Checkout({ amount }: { amount: string | null }) {
  const { getToken, userId } = useAuth();
  const { user } = useUser();
  const [opening, setOpening] = useState(false);
  const [arrival, setArrival] = useState<Arrival>({ state: "idle" });
  const [error, setError] = useState<string | null>(null);
  const credit = useRef<string>("0");
  const mine = useRef(false);

  /* Start loading Paddle.js now, so it is ready by the time Buy is pressed. Nothing on the page
     waits for it: the button is rendered the same on the server and in the browser, and `buy`
     awaits the load itself — a button gated on "has Paddle loaded" rendered disabled in the browser
     and enabled on the server, which React reports as a hydration mismatch. */
  useEffect(() => {
    let alive = true;
    loadPaddle().catch(() => alive && setError("The checkout did not load. Try reloading the page."));
    return () => {
      alive = false;
    };
  }, []);

  const waitFor = useCallback(
    async (transactionId: string) => {
      setArrival({ state: "waiting", credit: credit.current });
      const deadline = Date.now() + POLL_FOR_MS;
      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        try {
          const purchase = await getPurchase(transactionId, await getToken());
          if (purchase?.status === "credited") {
            setArrival({
              state: "credited",
              credit: purchase.credit_usd,
              balance: purchase.balance_usd,
            });
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
      setArrival({ state: "late" });
    },
    [getToken],
  );

  useEffect(() => {
    function onEvent(event: PaddleEventData) {
      if (!mine.current) return;
      if (event.name === "checkout.completed" && event.data?.transaction_id) {
        void waitFor(event.data.transaction_id);
        /* Back to our page on its own. Paddle's success screen otherwise stays up until the buyer
           finds "Return to …" — and what they want to see is their credit arriving, which this
           page says as soon as the webhook lands. A moment's pause first, so the payment is seen
           to have gone through. */
        setTimeout(() => void loadPaddle().then((loaded) => loaded?.Checkout.close()), CLOSE_AFTER_MS);
      }
      if (event.name === "checkout.closed") mine.current = false;
    }
    listeners.add(onEvent);
    return () => {
      listeners.delete(onEvent);
    };
  }, [waitFor]);

  async function buy() {
    if (!amount) return;
    setError(null);
    setArrival({ state: "idle" });
    setOpening(true);
    try {
      const paddle = await loadPaddle();
      if (!paddle || !userId) {
        setError("The checkout is not ready yet. Try again in a moment.");
        return;
      }
      const checkout = await startCheckout(amount, await getToken());
      credit.current = checkout.quote.credit_usd;
      mine.current = true;
      const email = user?.primaryEmailAddress?.emailAddress;
      paddle.Checkout.open({
        transactionId: checkout.transaction_id,
        ...(email ? { customer: { email } } : {}),
        settings: { displayMode: "overlay", variant: "one-page", theme: "dark", allowLogout: !email },
      });
    } catch (failure) {
      setError(
        failure instanceof ApiError
          ? failure.message
          : "That did not reach the server. Check your connection and try again.",
      );
    } finally {
      setOpening(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={() => void buy()}
        disabled={!amount || opening}
        className={BUY}
      >
        {amount ? `buy $${amount} of credit` : "buy"}
      </button>
      <ArrivalNote arrival={arrival} />
      {error && (
        <p role="alert" className="font-mono text-meta text-bad">
          {error}
        </p>
      )}
    </div>
  );
}

function ArrivalNote({ arrival }: { arrival: Arrival }) {
  switch (arrival.state) {
    case "idle":
      return null;
    case "waiting":
      return (
        <Note tone="text-ink-dim">Paid. Adding {formatBalance(arrival.credit)} to your credit…</Note>
      );
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

/** "5" from "5" or "5.00": the API's decimals, as the whole dollars the amounts always are. */
function whole(usd: string): string {
  return String(Math.trunc(Number(usd)));
}

/** "$5.00" from "5". The API's own figures, only formatted: the checkout shows what the buyer pays
 *  with their tax, and this page does no arithmetic. */
function dollars(usd: string): string {
  return `$${Number(usd).toFixed(2)}`;
}

const BUY =
  "border border-accent-deep bg-accent px-3 py-2 text-center font-mono text-meta uppercase tracking-[0.1em] text-on-accent transition-colors hover:bg-accent-dim disabled:opacity-40";
