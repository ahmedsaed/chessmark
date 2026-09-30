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
 * balance cannot cover it. Our dialog then opens over the page with Paddle's payment form embedded in
 * it (inline checkout), on that transaction, and Paddle's signed
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
import {
  initializePaddle,
  type CheckoutEventsData,
  type Paddle,
  type PaddleEventData,
} from "@paddle/paddle-js";
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
  /* Configured, but the operator has not opened sales or has paused them (ADR-0057). The breakdown
     and the tax estimate still show: what an amount comes to is worth knowing before it can be
     bought. */
  const closed = selling && availability.state === "paused";

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
  const estimate = useTaxEstimate(
    selling ? options.tax_preview_price_id : null,
    quote ? whole(quote.price_usd) : null,
  );
  /* With an estimate, the breakdown with that tax taken out; without, the no-tax one. */
  const shown = estimate?.quote ?? quote;
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
        {/* A sum, top to bottom: what the buyer pays — the amount they chose, tax included — then
            everything taken out of it, down to what reaches their balance. With a tax estimate the
            tax is the first line and the credit is "about"; the checkout confirms both. Every
            figure is the API's or Paddle's; the page adds nothing up. */}
        <div>
          <p className="font-mono text-meta uppercase tracking-[0.14em] text-ink-faint">You pay</p>
          <p className="tabular mt-1 font-serif text-4xl text-ink">
            {quote ? dollars(quote.price_usd) : "—"}
          </p>
        </div>
        <dl className="tabular flex flex-col gap-1.5 font-mono text-meta">
          {estimate && (
            <div className="flex items-baseline justify-between gap-3 text-ink-faint">
              <dt>Tax ({estimate.country}, estimated)</dt>
              <dd className="text-ink-dim">− {dollars(shown?.tax_usd ?? "0")}</dd>
            </div>
          )}
          <Line label="Payment processor" rule="5% + $0.50" amount={shown?.processor_fee_usd} />
          <Line label="Running Chessmark" rule="5%" amount={shown?.upkeep_usd} />
          <Line label="AI provider fee" rule="5.5%" amount={shown?.provider_fee_usd} />
          <div className="mt-1.5 flex items-baseline justify-between gap-3 border-t border-line pt-2.5">
            <dt className="uppercase tracking-[0.12em] text-ink">
              Your credit{estimate && <span className="text-ink-faint"> (about)</span>}
            </dt>
            <dd className="text-lg text-accent">{shown ? dollars(shown.credit_usd) : "—"}</dd>
          </div>
        </dl>
        <p className="-mt-2 text-xs text-ink-faint">
          {estimate
            ? "Tax is estimated from your location; the checkout confirms it, and your credit."
            : "Any tax comes out of this amount at checkout."}
        </p>
        {!selling || closed || soldOut || paused ? (
          <div className="flex flex-col gap-2">
            <button type="button" disabled className={BUY}>
              {!selling
                ? "not on sale yet"
                : closed
                  ? "not on sale right now"
                  : soldOut
                    ? "sold out for now"
                    : "paused for a moment"}
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

/**
 * The visitor's tax on an amount, estimated by Paddle from where they are browsing (ADR-0056).
 *
 * Paddle previews only catalogue prices, and purchases use a price the server creates per checkout,
 * so this previews a $1 price that is never sold, in the chosen quantity. It is an estimate: the
 * checkout confirms the tax once the buyer's country, VAT number or US ZIP code is known.
 *
 * `null` until Paddle answers for *this* amount — each answer is tagged with the amount it is for,
 * so a slow reply for an earlier choice is never shown against a later one — and whenever there is
 * nothing to estimate. The breakdown then has no tax line, and says tax comes out at checkout.
 */
function useTaxEstimate(
  priceId: string | null,
  amount: string | null,
): { country: string; quote: CreditQuote } | null {
  const [answer, setAnswer] = useState<{
    amount: string;
    country: string;
    quote: CreditQuote;
  } | null>(null);

  useEffect(() => {
    if (!priceId || !amount) return;
    let current = true;
    const timer = setTimeout(async () => {
      try {
        const paddle = await loadPaddle();
        if (!paddle || !current) return;
        const preview = await paddle.PricePreview({
          items: [{ priceId, quantity: Number(amount) }],
        });
        const line = preview.data.details.lineItems[0];
        const code = preview.data.address?.countryCode;
        if (!current || !line || !code) return;
        /* The API takes the estimated tax out of the amount; Paddle's cents go as they came. */
        const taxed = await getCreditQuote(amount, { cents: line.totals.tax });
        if (!current) return;
        setAnswer({
          amount,
          country: new Intl.DisplayNames(["en"], { type: "region" }).of(code) ?? code,
          quote: taxed,
        });
      } catch {
        /* No estimate is a fine answer: the breakdown shows the no-tax figures. */
      }
    }, QUOTE_AFTER_MS);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [priceId, amount]);

  return answer && answer.amount === amount ? answer : null;
}

/**
 * The checkout's own breakdown: the tax Paddle is about to charge, taken out of the amount by the
 * API. Tagged with the amount and tax it is for, so a change of country in the form never shows
 * the credit for the previous one.
 */
function useCheckoutQuote(amount: string | null, tax: number | undefined): CreditQuote | null {
  const [answer, setAnswer] = useState<{ key: string; quote: CreditQuote } | null>(null);
  const key = amount !== null && tax !== undefined ? `${amount}:${tax}` : null;

  useEffect(() => {
    if (!key || amount === null || tax === undefined) return;
    let current = true;
    getCreditQuote(amount, { dollars: String(tax) }).then(
      (quote) => {
        if (current) setAnswer({ key, quote });
      },
      () => {
        /* Keep the dashes; the webhook settles the credit whatever this shows. */
      },
    );
    return () => {
      current = false;
    };
  }, [key, amount, tax]);

  return answer && answer.key === key ? answer.quote : null;
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
  /* What Paddle says the checkout holds — item, subtotal, tax, total — for the summary beside the
     payment form. `null` until Paddle has loaded it, and again after the dialog closes. */
  const [summary, setSummary] = useState<CheckoutEventsData | null>(null);
  const [paid, setPaid] = useState(false);
  /* The credit this checkout grants, for the summary. Kept in state as well as in `credit`, which
     the poll reads: a ref must not be read while rendering. */
  const [granting, setGranting] = useState<CreditQuote | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
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

  const close = useCallback(() => {
    void loadPaddle().then((loaded) => loaded?.Checkout.close());
    mine.current = false;
    if (dialog.current?.open) dialog.current.close();
  }, []);

  useEffect(() => {
    function onEvent(event: PaddleEventData) {
      if (!mine.current) return;
      if (
        event.data &&
        (event.name === "checkout.loaded" ||
          event.name === "checkout.updated" ||
          event.name === "checkout.items.updated")
      ) {
        setSummary(event.data);
      }
      if (event.name === "checkout.completed" && event.data?.transaction_id) {
        setPaid(true);
        void waitFor(event.data.transaction_id);
        /* Back to the page on its own, where the credit is reported as it arrives. A moment's
           pause first, so the payment is seen to have gone through. */
        setTimeout(close, CLOSE_AFTER_MS);
      }
    }
    listeners.add(onEvent);
    return () => {
      listeners.delete(onEvent);
    };
  }, [waitFor, close]);

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
      setGranting(checkout.quote);
      mine.current = true;
      setSummary(null);
      setPaid(false);
      /* The dialog first, so the element Paddle renders into exists when it looks for it. */
      dialog.current?.showModal();
      const email = user?.primaryEmailAddress?.emailAddress;
      paddle.Checkout.open({
        transactionId: checkout.transaction_id,
        ...(email ? { customer: { email } } : {}),
        settings: {
          displayMode: "inline",
          variant: "one-page",
          theme: "dark",
          frameTarget: FRAME_TARGET,
          frameInitialHeight: 450,
          /* `color-scheme: light` is what makes the frame see-through. The site declares
             `color-scheme: dark`, and a browser gives an embedded page whose scheme differs from
             its host an opaque canvas — white, behind Paddle's dark form — so its text stays
             legible. Matching the frame's own scheme removes that canvas, and our dialog shows
             through. It changes nothing inside the frame. */
          frameStyle:
            "width: 100%; min-width: 312px; background-color: transparent; border: none; color-scheme: light;",
          showAddDiscounts: false,
          allowLogout: !email,
        },
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

      {/* Our own dialog, over the page it was opened from — blurred, not replaced, so the buyer
          can see they have not left Chessmark. The backdrop's colour and blur are literal values:
          `::backdrop` does not inherit the page's custom properties in every browser (Firefox),
          and Tailwind's `bg-ground/50` and `backdrop-blur-md` are both variables underneath, so
          there they drew nothing at all. Paddle's payment form is embedded in it (inline
          checkout), and the order summary beside it is ours, drawn from what Paddle reports: an
          inline checkout must show what is bought, the subtotal, tax and total with their
          currency, Paddle's own footer, and the refund policy. */}
      <dialog
        ref={dialog}
        aria-labelledby="checkout-title"
        onClose={() => {
          mine.current = false;
          void loadPaddle().then((loaded) => loaded?.Checkout.close());
        }}
        className="m-auto max-h-[calc(100dvh-1rem)] w-[min(960px,calc(100vw-1rem))] max-w-none overflow-y-auto overflow-x-hidden border border-line bg-ground p-0 text-ink backdrop:bg-[rgba(22,19,14,0.55)] backdrop:[backdrop-filter:blur(6px)]"
      >
        <div className="flex items-center justify-between border-b border-line px-3 py-3 sm:px-5 sm:py-4">
          <h2 id="checkout-title" className="font-serif text-2xl text-ink">
            Buy credit
          </h2>
          <button
            type="button"
            onClick={close}
            aria-label="Close the checkout"
            className="border border-line bg-surface px-2.5 py-1 font-mono text-meta text-ink-faint transition-colors hover:border-accent-dim hover:text-ink"
          >
            ✕
          </button>
        </div>
        {/* Tight on a phone: Paddle's form needs 312px, which a 390px screen only just has. */}
        <div className="grid gap-6 p-3 sm:p-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
          <OrderSummary summary={summary} quote={granting} paid={paid} />
          <div className={FRAME_TARGET} />
        </div>
      </dialog>
    </div>
  );
}

/** The class Paddle renders its payment form into. */
const FRAME_TARGET = "paddle-checkout-frame";

/**
 * The purchase as one sum, top to bottom: what the buyer pays — the amount they chose, tax
 * included — then everything taken out of it, the tax first, down to the credit.
 *
 * The tax is what Paddle's checkout is about to charge, so it changes when the buyer changes
 * country in the form; the API takes it out of the amount and quotes the rest. The lines wait as
 * dashes until the API has answered for that tax. The webhook settles the credit from the tax
 * Paddle finally charges, which is this figure.
 */
function OrderSummary({
  summary,
  quote,
  paid,
}: {
  summary: CheckoutEventsData | null;
  quote: CreditQuote | null;
  paid: boolean;
}) {
  const exact = useCheckoutQuote(quote ? whole(quote.price_usd) : null, summary?.totals.tax);
  return (
    <div className="flex flex-col gap-5">
      <div>
        <p className="font-mono text-meta uppercase tracking-[0.14em] text-ink-faint">You pay</p>
        <p className="tabular mt-1 font-serif text-4xl text-ink">
          {quote ? dollars(quote.price_usd) : "—"}
        </p>
      </div>
      <dl className="tabular flex flex-col gap-1.5 font-mono text-meta">
        <SummaryLine label="Tax" value={exact ? `− ${dollars(exact.tax_usd)}` : "—"} />
        <Line label="Payment processor" rule="5% + $0.50" amount={exact?.processor_fee_usd} />
        <Line label="Running Chessmark" rule="5%" amount={exact?.upkeep_usd} />
        <Line label="AI provider fee" rule="5.5%" amount={exact?.provider_fee_usd} />
        <div className="mt-1.5 flex items-baseline justify-between gap-3 border-t border-line pt-2.5">
          <dt className="uppercase tracking-[0.12em] text-ink">Your credit</dt>
          <dd className="text-lg text-accent">{exact ? dollars(exact.credit_usd) : "—"}</dd>
        </div>
      </dl>

      <p className="text-sm leading-relaxed text-ink-dim">
        {paid
          ? "Paid. Adding the credit to your account…"
          : "The credit is added as soon as the payment goes through."}
      </p>
      <p className="text-xs text-ink-faint">
        Sold by Paddle, our merchant of record. See our{" "}
        <Link href="/refunds" className="text-accent underline underline-offset-4">
          refund policy
        </Link>
        .
      </p>
    </div>
  );
}

function SummaryLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-ink-faint">
      <dt>{label}</dt>
      <dd className="text-ink-dim">{value}</dd>
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
