import type { Metadata } from "next";

import { Clause, InlineLink, LegalPage, Points, SupportEmail } from "@/components/LegalPage";
import { pageMetadata } from "@/lib/site";

export const metadata: Metadata = pageMetadata({
  title: "Privacy policy",
  description: "What Chessmark collects, what is public, who receives it, and how to delete it.",
  path: "/privacy",
});

/**
 * The privacy policy.
 *
 * Each claim here is a fact about the code, and must change with it:
 * - `users` holds the Clerk id, email, display name and balance; nothing else about a person.
 * - A model request carries the game's id as `session_id` and never the player's identity.
 * - The only cookies are Clerk's session cookies. There is no analytics or advertising script.
 * - Deleting an account (Clerk's `user.deleted`) removes the user and their ledgers by cascade,
 *   and leaves their games in place with the seat unattributed (`players.user_id` SET NULL).
 */
export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy policy"
      updated="27 September 2026"
      summary={
        <p>
          Chessmark collects what it needs to run your account and your games, and nothing for
          advertising. It has no trackers and sells no data. The part to know is that games are
          public, including the messages you type in them.
        </p>
      }
    >
      <Clause title="Who is responsible">
        <p>
          Chessmark is made by Merope (merope.dev), the trading name of Ahmed Saed, an individual
          based in Egypt, who is responsible for the personal data described here. Contact:{" "}
          <SupportEmail />.
        </p>
      </Clause>

      <Clause title="What we collect">
        <Points>
          <li>
            <strong className="text-ink">Your account:</strong> your email address and name.
            Sign-in is handled by Clerk. If you sign in with Google, Google shares your name, email
            address and profile picture with Clerk. We never see your password.
          </li>
          <li>
            <strong className="text-ink">Your games:</strong> the games you play and start, your
            moves, and the messages you type in them.
          </li>
          <li>
            <strong className="text-ink">Your credit:</strong> your balance, and every charge, grant
            and correction to it, with the game it was for.
          </li>
          <li>
            <strong className="text-ink">Purchases:</strong> Paddle, which sells credit, collects
            your payment details and billing address. We receive the purchase itself: the amount,
            the date, your email and country. We never see your card.
          </li>
          <li>
            <strong className="text-ink">Technical logs:</strong> our servers log requests,
            including IP addresses and browser type, to keep the service running and secure.
          </li>
        </Points>
        <p>
          Cookies are used only to keep you signed in. There are no analytics or advertising
          cookies, and nothing tracks you across other sites.
        </p>
      </Clause>

      <Clause title="What is public">
        <p>
          Games are public: anyone can watch them and replay them, including the moves and messages
          of games you play. Your display name is shown on the games you play and the games you
          start. Your email address, balance and payments are never shown to anyone else.
        </p>
      </Clause>

      <Clause title="How it is used">
        <Points>
          <li>to run your account, your games and your credit, and to answer you;</li>
          <li>to keep the service secure and stop abuse;</li>
          <li>
            to publish games and compute the benchmark&apos;s ratings and statistics, which are about
            the models, not about you.
          </li>
        </Points>
        <p>
          If you are in the EU or the UK, the legal bases are: providing the service you asked for
          (account, games, credit), our legitimate interest in security and in publishing the
          benchmark (public games), and legal obligations (payment records).
        </p>
      </Clause>

      <Clause title="AI providers">
        <p>
          To make a model move, the game goes to OpenRouter and the company that runs the model.
          That includes the moves and every message in it, including the ones you typed. We send
          the game&apos;s id, not your name or email. Those providers handle it under their own
          privacy policies, which may include keeping it.
        </p>
      </Clause>

      <Clause title="Who else receives data">
        <Points>
          <li>
            <strong className="text-ink">Clerk</strong>: sign-in and your account details;
          </li>
          <li>
            <strong className="text-ink">Google</strong>: only if you sign in with it;
          </li>
          <li>
            <strong className="text-ink">OpenRouter and the AI providers</strong>: game content,
            as above;
          </li>
          <li>
            <strong className="text-ink">Paddle</strong>: your purchases and payment details;
          </li>
          <li>
            <strong className="text-ink">Our hosting provider</strong>: it stores the servers
            everything runs on.
          </li>
        </Points>
        <p>
          We do not sell personal data or share it for advertising. We disclose it only if the law
          requires us to. These companies may process data outside your country, including in the
          United States.
        </p>
      </Clause>

      <Clause title="How long we keep it">
        <p>
          Your account, credit history and email are kept until your account is deleted. Game
          records are kept permanently, because the benchmark is measured on them. When an account
          is deleted, its games stay public without the player&apos;s name. Paddle keeps its own
          payment records for as long as the law requires.
        </p>
      </Clause>

      <Clause title="Your rights">
        <p>
          You can ask for a copy of your data, ask us to correct it, or ask us to delete your
          account. Write to <SupportEmail /> from the email address on your account. If a message
          you typed in a game contains personal information you want removed, write to us too.
        </p>
        <p>
          If you are in the EU or the UK, you can also object to how your data is used, ask for it
          in a portable form, and complain to your data protection authority.
        </p>
      </Clause>

      <Clause title="Children">
        <p>
          Chessmark is not for children under 13. If we learn that a child under 13 has an account,
          we delete it.
        </p>
      </Clause>

      <Clause title="Changes">
        <p>
          When this policy changes, the date at the top changes too. If a change matters to you, we
          will tell you by email or on the site. The{" "}
          <InlineLink href="/terms">terms of service</InlineLink> cover the rest of how the site
          works.
        </p>
      </Clause>
    </LegalPage>
  );
}
