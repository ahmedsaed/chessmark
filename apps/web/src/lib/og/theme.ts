/**
 * The palette a social card is drawn in, and the font it needs to draw pieces.
 *
 * **The values are literal hex, and they have to be.** A card is rendered by Satori on the server
 * with no stylesheet and no cascade, so `var(--color-ink)` resolves to nothing and the element
 * comes out transparent. These are the same values `globals.css` sets on `@theme`, copied once
 * here rather than once per card.
 *
 * Copied once matters: the two cards that existed before this module had drifted apart. The game
 * card drew the board in `#b3a795`/`#5f5445` and the root card in `#9c8869`/`#4b3f2f`, and only
 * the second pair was the design system's. Two links to the same site produced two different
 * boards, which is exactly the thing a shared card is for. The token values win.
 *
 * If a token in `globals.css` changes, it changes here too — there is no way to make that
 * automatic without shipping the stylesheet to the renderer, so `og.test.ts` pins the pairs that
 * must agree instead.
 */

/** Every card is 1200×630 — the size every unfurler crops to. */
export const CARD = { width: 1200, height: 630 } as const;
export const CONTENT_TYPE = "image/png";

/**
 * How long a generated card may be served before it is drawn again.
 *
 * **Every card reads live data, so without this each unfurl is an API round trip and a Satori
 * render.** A link posted in a busy channel is fetched by every client that renders a preview, and
 * crawlers re-fetch on their own schedule; none of that is traffic a person is waiting on.
 *
 * Five minutes, and the number is a judgement rather than a measurement: a rating that moved two
 * minutes ago is not wrong on a card, and `/leaderboard` itself is a stored ranking rebuilt only
 * when the games disagree with it (ADR-0032). A card is a *picture of* that page, so it can afford
 * to be staler than the page is.
 *
 * **The number is written as a literal in each route, and this constant is not imported there.**
 * Next requires a segment config export to be statically analysable — an imported binding fails the
 * production build with "Invalid segment configuration export detected", and only `next build` says
 * so. This is the one place the *decision* lives; the routes carry the value and a pointer back.
 */
export const REVALIDATE_SECONDS = 300;

/** The design system's values, from `globals.css`'s `@theme`. */
export const COLOUR = {
  ground: "#16130e",
  surface: "#1e1a14",
  surface2: "#272219",
  line: "#3a3227",
  lineSoft: "#2c2619",

  ink: "#efe8da",
  inkDim: "#a99e8b",
  inkFaint: "#756b5b",

  accent: "#dca84b",
  machine: "#63bcc8",
  good: "#82b36b",
  bad: "#c86a55",

  squareLight: "#9c8869",
  squareDark: "#4b3f2f",
  pieceWhite: "#f5efe2",
  pieceBlack: "#14110c",
} as const;
