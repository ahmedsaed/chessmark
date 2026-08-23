"use client";

/**
 * The root error boundary.
 *
 * Next.js 16 renamed this prop from `reset` to `retry` — the older name silently does nothing,
 * so the button would render and never recover.
 *
 * The most likely cause here by far is the API being unreachable, which is why the copy names
 * that rather than saying "something went wrong".
 */

import { useEffect } from "react";

export default function GlobalError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="mx-auto flex w-full max-w-[640px] flex-1 flex-col justify-center px-5 py-24">
      <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-bad">Error</p>
      <h1 className="mt-3 font-serif text-4xl leading-tight text-ink">That did not load.</h1>
      <p className="mt-4 text-ink-dim">
        Usually the API is not reachable. The board, the transcripts, and the ratings all come
        from it, so the page has nothing to show until it answers.
      </p>
      {error.digest && (
        <p className="tabular mt-3 font-mono text-[10px] text-ink-faint">digest {error.digest}</p>
      )}
      <div className="mt-8">
        <button
          type="button"
          onClick={() => retry()}
          className="border border-accent-deep bg-accent px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.14em] text-on-accent transition-colors hover:bg-accent-dim"
        >
          Try again
        </button>
      </div>
    </main>
  );
}
