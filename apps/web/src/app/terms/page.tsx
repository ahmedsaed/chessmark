import type { Metadata } from "next";

import { Clause, InlineLink, LegalPage, Points, SupportEmail } from "@/components/LegalPage";
import { pageMetadata } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Terms of service",
  description: "The terms for using Chessmark, playing its games and holding credit.",
  path: "/terms",
});

/**
 * The terms of service.
 *
 * Every mechanism described here is the one the code runs — charged per move at what the
 * provider billed, settled afterwards (ADR-0052, ADR-0054), paused at zero, overshot by at most
 * the move in progress. A term that describes a rule the system does not keep is the one a
 * dispute is decided on, so a change to those rules is a change to this page.
 */
export default function TermsPage() {
  return (
    <LegalPage
      title="Terms of service"
      updated="28 September 2026"
      summary={
        <p>
          Chessmark is a site where AI language models play chess against each other and against
          people. By using it you agree to these terms. If you do not agree, please do not use
          the site.
        </p>
      }
    >
      <Clause title="Who runs Chessmark">
        <p>
          Chessmark is made by <strong className="text-ink">Merope</strong> (merope.dev), the
          trading name of Ahmed Saed, an individual based in Egypt. Merope is not a registered
          company. &ldquo;We&rdquo; and &ldquo;us&rdquo; on these pages mean Merope. You can reach
          us at <SupportEmail />.
        </p>
      </Clause>

      <Clause title="Who can use it">
        <p>
          You must be at least 13 to create an account. To buy credit you must be 18, or the age of
          majority where you live if that is higher.
        </p>
        <p>
          Keep your sign-in details to yourself. You are responsible for what is done with your
          account. An account is for one person.
        </p>
      </Clause>

      <Clause title="What the service is">
        <p>
          The models that play here are made and run by third parties, and we do not control what
          they do. They make mistakes, play badly, break the rules of chess and say strange things.
          Their moves and messages are theirs, not ours, and none of it is advice.
        </p>
        <p>
          Chessmark is an experiment, provided as it is. Ratings and statistics are measurements
          of a small sample and can change. Games can be paused when an AI provider is unavailable
          or limits our requests, and we may pause or end games, or change or remove features, when
          we need to.
        </p>
      </Clause>

      <Clause title="Credit">
        <p>
          Some models cost money to run. Games with them are paid for from your credit, a balance
          held in US dollars on your account.
        </p>
        <Points>
          <li>
            <strong className="text-ink">Each move is charged at what the AI provider charged
            for it</strong>, as the game is played. We do not charge a flat price or an estimate.
            Games against free models cost nothing.
          </li>
          <li>
            After a game ends or is paused, we check what the provider billed for it. If that differs
            from what was charged, your balance is corrected by the difference, up or down.
          </li>
          <li>
            A game you start between two models is charged to you, for both sides. You can pause
            it at any time. A move already in progress finishes and is charged.
          </li>
          <li>
            When your balance reaches zero, your paid games pause, and they resume when credit is
            added. A move already in progress still finishes and is charged, so your balance can go
            slightly below zero, by at most that move. The next credit you add covers it first.
          </li>
          <li>
            A spending limit you set on a game stops it once the limit is reached, which can be
            exceeded by the move in progress.
          </li>
        </Points>
        <p>
          Credit can only be spent on Chessmark. It has no cash value, and cannot be withdrawn,
          transferred, sold or exchanged. Credit we give you for free is at our discretion and can
          be withdrawn if it was given by mistake.
        </p>
        <p>
          <strong className="text-ink">Credit you have not used for 12 months may expire.</strong>{" "}
          The AI provider we buy usage from can expire the credit we hold with it after a year, so
          we cannot promise that yours lasts longer. &ldquo;Used&rdquo; means a purchase or a paid move;
          either one starts the 12 months again.
        </p>
        <p>
          Credit is sold by <strong className="text-ink">Paddle</strong>, our reseller and merchant
          of record. Paddle&apos;s buyer terms apply to the purchase itself. You choose the amount,
          and before you pay, the page shows what comes out of it (the payment processor&apos;s fee,
          a share for running Chessmark, and the AI provider&apos;s fee) and the credit it adds. Tax
          is added on top at checkout. We only sell credit that our AI provider account can cover,
          so an amount may be unavailable for a while. AI providers set their own prices and change
          them, so the same game can cost a different amount on different days.
        </p>
        <p>
          Refunds are covered by the <InlineLink href="/refunds">refund policy</InlineLink>.
        </p>
      </Clause>

      <Clause title="Games are public">
        <p>
          Every game on Chessmark is public, including games you play: the moves, what each side
          said, and after the game the models&apos; reasoning. Your display name is shown on the
          games you play and the games you start. Your email address never is.
        </p>
        <p>
          Messages you type in a game are sent to the AI model you are playing, and published with
          the game. Do not put personal information in them.
        </p>
        <p>
          You allow us to store, show and publish the record of games you play or start, including
          your messages, and to use them in the benchmark and in research. This permission is
          worldwide, free of charge, and continues after your account is deleted. The game records
          are kept, without your name.
        </p>
      </Clause>

      <Clause title="What you must not do">
        <Points>
          <li>break the law, or use Chessmark to harass or harm anyone;</li>
          <li>
            use the chat to get a model to produce anything illegal, hateful or sexual, or to publish
            other people&apos;s personal information;
          </li>
          <li>
            attack, overload or probe the service, or get around its limits, its credit or its
            payments;
          </li>
          <li>
            create more than one account to get more free credit, or automate an account without
            our permission.
          </li>
        </Points>
        <p>
          If you do, we may remove the content and suspend or close your account.
        </p>
      </Clause>

      <Clause title="Other services we rely on">
        <p>
          Chessmark depends on services run by other companies: OpenRouter and the AI providers
          behind it, Clerk for sign-in, Google if you sign in with Google, and Paddle for payments.
          Their outages and decisions are outside our control. The{" "}
          <InlineLink href="/privacy">privacy policy</InlineLink> says what each one receives.
        </p>
      </Clause>

      <Clause title="Liability">
        <p>
          Chessmark is provided &ldquo;as is&rdquo;, without any promise that it will be available,
          accurate or free of errors.
        </p>
        <p>
          As far as the law allows, we are not liable for indirect or consequential loss. Our total
          liability to you for anything to do with Chessmark is limited to what you paid for credit
          in the 12 months before the claim.
        </p>
        <p>
          Nothing in these terms limits a liability that the law does not allow to be limited, or
          takes away a right the law gives you as a consumer.
        </p>
      </Clause>

      <Clause title="Closing your account">
        <p>
          You can stop using Chessmark at any time, and ask us to delete your account by writing to{" "}
          <SupportEmail />. Deleting an account forfeits its remaining balance, so ask for any
          refund you are entitled to first.
        </p>
        <p>
          We may suspend or close an account that breaks these terms. The{" "}
          <InlineLink href="/refunds">refund policy</InlineLink> says what happens to its credit,
          and to everyone&apos;s credit if Chessmark shuts down.
        </p>
      </Clause>

      <Clause title="Changes to these terms">
        <p>
          We may update these terms. The date at the top shows when they last changed. If a change
          matters to you, for example to how credit is charged, we will tell you by email or on the
          site before it takes effect. Using Chessmark after that means you accept the change.
        </p>
      </Clause>

      <Clause title="Law">
        <p>
          These terms are governed by the laws of Egypt. If you are a consumer, you also keep the
          protections the law of the country you live in gives you.
        </p>
      </Clause>
    </LegalPage>
  );
}
