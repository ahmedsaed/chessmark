/**
 * Reading a URL out of the environment.
 *
 * `??` is the obvious way to apply a default and it is not enough, because **unset and empty
 * arrive by different routes and only one of them is nullish.** A GitHub Actions variable that
 * was never created interpolates to `""`; `docker build` forwards that as an empty build arg;
 * `process.env.X ?? "…"` then keeps the empty string. The API base URL became `""`, so every
 * server-side fetch became a *relative* one — and Next's patched fetch neither resolves nor
 * rejects a relative URL during a prerender, it hangs. `next build` died on a 60-second timeout
 * for `/sitemap.xml`, naming no URL anywhere in the error, and the web image was never published
 * while the backend image was.
 *
 * Compose was immune the whole time, which is why this never showed up locally:
 * `${VAR:-default}` treats empty as absent. `??` does not.
 */

/** An origin from the environment, or `fallback` when it is unset, empty, or only whitespace. */
export function originFromEnv(value: string | undefined, fallback: string): string {
  const trimmed = value?.trim();
  /* A trailing slash would make every request path a `//games`. Cheap to tolerate here. */
  return (trimmed || fallback).replace(/\/+$/, "");
}
