import type { Metadata } from "next";

import { Clause, InlineLink, LegalPage, Points, SupportEmail } from "@/components/LegalPage";
import { pageMetadata } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Refund policy",
  description: "When credit is refunded on Chessmark, and how to ask for it.",
  path: "/refunds",
});

/**
 * The refund policy (owner's decisions, 2026-09-27).
 *
 * Spent credit is not refunded, because what it paid for — the moves — was delivered
 * (ADR-0052). The exceptions are the ones the law and the reseller impose anyway, stated as one
 * plain rule: an untouched purchase within 14 days. EU and UK buyers have that right by statute,
 * and Paddle may refund within 14 days whatever this page says, so promising less would only make
 * the page wrong.
 */
export default function RefundsPage() {
  return (
    <LegalPage
      title="Refund policy"
      updated="27 September 2026"
      summary={
        <p>
          Credit pays for AI moves as they are played, at what the AI provider charged for them.
          Once a move has been played, the credit it used is not refunded, whether or not the game
          reached a result. The exceptions are below.
        </p>
      }
    >
      <Clause title="Unused credit">
        <p>
          Credit you have bought is not refundable for cash, with one exception: you can ask for a
          full refund of a purchase <strong className="text-ink">within 14 days</strong> of making
          it, as long as you have not spent any credit since. The money goes back to the payment
          method you used.
        </p>
        <p>
          Once you have played a paid move after a purchase, that purchase is no longer
          refundable. The rest of your balance stays on your account, and it does not expire.
        </p>
      </Clause>

      <Clause title="Broken games">
        <p>
          If a game went wrong because of a fault in Chessmark itself, we give the credit it cost
          you back to your balance. Examples of a fault:
        </p>
        <Points>
          <li>the game stopped, stalled or ended in a way the rules of chess do not explain;</li>
          <li>you were charged for moves that never reached the board;</li>
          <li>a charge on your account does not match the game it names.</li>
        </Points>
        <p>
          <strong className="text-ink">Tell us within 30 days</strong> at <SupportEmail />, from
          the email address on your account, with a link to the game and what went wrong. If we
          confirm the fault, the credit the game used is returned to your Chessmark balance. It is
          returned as credit, not as cash.
        </p>
        <p>These are not faults, and are not refunded:</p>
        <Points>
          <li>
            a model playing badly, losing, making illegal moves or forfeiting. That is what is
            being measured;
          </li>
          <li>anything a model said during a game;</li>
          <li>a game that was slow, or that reached a result you did not expect;</li>
          <li>
            a game paused because an AI provider was unavailable or your credit ran out. Nothing is
            charged while a game is paused.
          </li>
        </Points>
      </Clause>

      <Clause title="If we close your account or the service">
        <p>
          If Chessmark shuts down, or we close your account without you having broken the{" "}
          <InlineLink href="/terms">terms</InlineLink>, we refund the credit you bought and have
          not spent to your original payment method. Credit we gave you for free is not refunded.
        </p>
        <p>
          If we close your account because you broke the terms, your remaining credit is not
          refunded.
        </p>
      </Clause>

      <Clause title="Who processes refunds">
        <p>
          Credit is sold by Paddle, our reseller and merchant of record, so the payment and any
          refund of it go through Paddle. Paddle&apos;s buyer terms apply to your purchase, and
          Paddle may also refund a purchase under its own policy. Nothing on this page limits a
          right the law gives you as a consumer.
        </p>
        <p>
          When a purchase is refunded or charged back, the credit it added is taken off your
          balance. That can leave your balance below zero, in which case your paid games pause
          until it is back above zero.
        </p>
      </Clause>

      <Clause title="How to ask">
        <p>
          Email <SupportEmail /> from the email address on your account. Include the date of the
          purchase or the link to the game.
        </p>
      </Clause>
    </LegalPage>
  );
}
