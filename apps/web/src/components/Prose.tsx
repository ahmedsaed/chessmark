import Link from "next/link";

import type { Sentence } from "@/lib/prose";

/**
 * A sentence from `lib/prose`, with its links. A link inside a sentence is underlined, always:
 * the accent is 1.2:1 against the prose around it, and colour alone would not mark it
 * (FRONTEND.md).
 */
export function SentenceText({ sentence }: { sentence: Sentence }) {
  return (
    <>
      {sentence.map((part, index) =>
        typeof part === "string" ? (
          part
        ) : (
          <Link
            key={index}
            href={part.href}
            /* Every link here is to a model or an event: dynamic routes, which prefetch in a
               loop while on screen (FRONTEND.md). */
            prefetch={false}
            className="text-accent underline underline-offset-4"
          >
            {part.text}
          </Link>
        ),
      )}
    </>
  );
}

/** Sentences run together as one paragraph. */
export function Paragraph({ sentences, className }: { sentences: Sentence[]; className?: string }) {
  return (
    <p className={className}>
      {sentences.map((sentence, index) => (
        <span key={index}>
          {index > 0 && " "}
          <SentenceText sentence={sentence} />
        </span>
      ))}
    </p>
  );
}
