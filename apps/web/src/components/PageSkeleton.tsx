/**
 * The placeholder a route shows while its server render is in flight.
 *
 * Every page is `force-dynamic` — a live game and a leaderboard are both wrong the moment they are
 * cached — and without a Suspense boundary Next.js has nowhere to yield, so a click did nothing at
 * all until the whole render finished: no spinner, no route change, no indication the click
 * landed. Several hundred milliseconds of that reads as a broken link rather than a slow one.
 *
 * **Only routes that cannot 404 get one.** A `loading.tsx` makes its segment stream, and a
 * streamed response commits its status line before the page body runs — so `notFound()` renders
 * the right page under a `200`. The browser suite asserts the status, which is how that was
 * caught; the routes that call `notFound()` resolve their existence check first and stream only
 * what comes after it.
 */
export function PageSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12" aria-busy="true">
      <span className="sr-only">Loading…</span>
      <div className="h-8 w-64 animate-pulse bg-surface-2" />
      <div className="mt-6 flex flex-col gap-3">
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className="h-14 animate-pulse bg-surface-2" />
        ))}
      </div>
    </main>
  );
}
