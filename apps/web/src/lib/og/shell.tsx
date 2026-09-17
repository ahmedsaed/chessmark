/**
 * The furniture every social card shares: the frame, the wordmark, and the few text shapes.
 *
 * A card exists to say *which site this is* before anyone clicks, so the parts that identify the
 * site should be identical on all of them and the parts that identify the page should be the only
 * thing that differs. Writing the wordmark into each card by hand is how two of them ended up with
 * different letter-spacing and different accents.
 *
 * **Every element carries `display: flex`.** Satori has no block layout: an element without it
 * either disappears or throws, and a `<div>` with two children and no display is the single most
 * common way to get a blank card. The primitives here are as much about not forgetting that as
 * about not repeating the padding.
 */

import { clip } from "@/lib/og/clip";
import { CARD, COLOUR } from "@/lib/og/theme";

/**
 * The outer frame: a board on the left, a panel of words on the right.
 *
 * **Both columns are given explicit widths, and that is not belt-and-braces.** Satori does not
 * resolve `flex: 1` — nor `flexGrow`/`flexBasis` longhand — into "the space left over" the way a
 * browser does. A panel left to negotiate its width came out sized by its widest child, overflowed
 * the card to the right, and starved the standings' name column to 0px, so the leaderboard rendered
 * as five ratings beside an empty column with the stats row running off the edge. Both attempts to
 * fix it inside the flex model produced byte-identical output.
 *
 * So the arithmetic is done here, once, rather than negotiated five times: the board is as wide as
 * its squares, and the panel is everything that is left. A card that wants no board passes none and
 * the panel takes the full width.
 *
 * `padding` is deliberately generous: an unfurler crops to its own aspect ratio, and every client
 * crops differently, so anything closer to the edge than this is something somebody will not see.
 */
const PADDING = 68;
const GAP = 56;

/** A board's drawn width: eight squares plus the 2px border either side. */
export function boardWidth(square: number): number {
  return square * 8 + 4;
}

export function Card({
  board,
  square = 54,
  children,
}: {
  /** The board to sit on the left, already sized with the same `square`. */
  board?: React.ReactNode;
  square?: number;
  children: React.ReactNode;
}) {
  const aside = board ? boardWidth(square) + GAP : 0;
  const panel = CARD.width - PADDING * 2 - aside;

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        alignItems: "center",
        gap: board ? GAP : 0,
        padding: `0 ${PADDING}px`,
        background: COLOUR.ground,
        color: COLOUR.ink,
      }}
    >
      {board}
      <div style={{ display: "flex", flexDirection: "column", width: panel, minWidth: 0 }}>
        {children}
      </div>
    </div>
  );
}

/**
 * A card with nothing to put beside the words.
 *
 * A model that has never played has no position of its own to show, and `Card` + `Panel` left the
 * text in the left third of a 1200px image with the rest empty — which reads as a card that failed
 * to load rather than one with less to say. Centred, and with the type a size up, the same content
 * fills the frame.
 */
export function CentredCard({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "0 90px",
        background: COLOUR.ground,
        color: COLOUR.ink,
      }}
    >
      {children}
    </div>
  );
}

/**
 * `CHESSMARK`, and optionally what part of it this is.
 *
 * The section label sits beside the wordmark rather than under the title, because a reader scanning
 * a timeline gets one glance: "CHESSMARK · LEADERBOARD" answers *what am I looking at* in that
 * glance, and a title alone does not.
 */
export function Wordmark({ section }: { section?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 14, fontSize: 21 }}>
      <div style={{ display: "flex", letterSpacing: 8, color: COLOUR.accent }}>CHESSMARK</div>
      {section && (
        <>
          <div style={{ display: "flex", color: COLOUR.inkFaint }}>·</div>
          <div style={{ display: "flex", letterSpacing: 5, color: COLOUR.inkFaint }}>
            {section.toUpperCase()}
          </div>
        </>
      )}
    </div>
  );
}

const LINE_HEIGHT = 1.12;

/**
 * The card's headline.
 *
 * **`lines` is a budget the text is cut to, not a height it is clipped at.** The first version set
 * `maxHeight` and `overflow: hidden` and let anything taller disappear, which is silent by
 * construction: the site's own card read *"Language models play chess. Everything is"* — the
 * tagline stopping mid-sentence, on the most-shared URL there is, looking entirely deliberate.
 *
 * A headline that does not fit should say so with `...`, or be given the room. Both are decisions;
 * a clipped sentence is neither.
 *
 * The budget is in characters because that is what `clip` can work in, and it is approximate on
 * purpose — glyph widths vary, the estimate is deliberately conservative, and the `maxHeight`
 * stays as a backstop so a bad estimate costs a truncated line rather than a card with its stats
 * pushed off the bottom.
 */
export function Title({
  children,
  size = 52,
  lines = 2,
}: {
  children: React.ReactNode;
  size?: number;
  /** How many lines the headline may use. Give it enough for the words you mean to show. */
  lines?: number;
}) {
  // ~0.52em per glyph across this face at these sizes; measured against the rendered cards.
  const perLine = Math.floor((CARD.width - 68 * 2 - 56 - 436) / (size * 0.52));

  return (
    <div
      style={{
        display: "flex",
        marginTop: 20,
        fontSize: size,
        lineHeight: LINE_HEIGHT,
        maxHeight: Math.ceil(size * LINE_HEIGHT * lines) + 4,
        overflow: "hidden",
      }}
    >
      {typeof children === "string" ? clip(children, perLine * lines) : children}
    </div>
  );
}

export function Subtitle({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", marginTop: 18, fontSize: 24, color: COLOUR.inkDim }}>
      {children}
    </div>
  );
}

/**
 * A row of figures along the foot of the card.
 *
 * Each is a value over a label, because that is how the site's own stat blocks read and a card
 * that reads differently from the page it links to is a small lie about where the link goes.
 */
export function Stats({ items }: { items: { value: string; label: string; tone?: string }[] }) {
  return (
    <div style={{ display: "flex", marginTop: 32, gap: 40 }}>
      {items.map((item) => (
        <div key={item.label} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ display: "flex", fontSize: 34, color: item.tone ?? COLOUR.ink }}>
            {item.value}
          </div>
          <div style={{ display: "flex", fontSize: 17, letterSpacing: 2, color: COLOUR.inkFaint }}>
            {item.label.toUpperCase()}
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * A ranked list — the leaderboard's rows, or a pool's table.
 *
 * Fixed-width columns rather than a table: Satori has no table layout, and a flex row whose cells
 * size to their content produces ragged numbers down the card.
 *
 * **`whiteSpace: "nowrap"` is doing the load-bearing work, and leaving it out was spectacular.**
 * A name cell of `flex: 1; minWidth: 0` is given almost no width by Satori when the text is one
 * long unbreakable token, so `nvidia/nemotron-3-ultra-550b-a55b:free` wrapped a character per line,
 * `overflow: hidden` clipped every one of them, and the row rendered **blank but tall**. Five of
 * those pushed the wordmark off the top of the card and the stats row out through the right edge:
 * the leaderboard's card came out as four ratings floating beside an empty column. It still
 * returned `200 image/png` at 46 KB, which is exactly why the byte-size check in `site.spec.ts`
 * did not notice and a person looking at the picture did in about a second.
 *
 * So the name is told not to wrap, and the figure is given a width rather than being sized by its
 * content — a rating and a score are both short, and a fixed column is what stops the name
 * negotiating with them for space.
 *
 * **And the truncation is done in the text, not in CSS.** `textOverflow: "ellipsis"` needs a `…`
 * to draw, Satori looks for it in the fonts it was given, and the only one it was given is a
 * six-glyph chess subset — so every truncated row ended in a tofu box. Three ASCII periods exist in
 * every face there will ever be.
 */

export function Standings({
  rows,
  highlightFirst = true,
}: {
  rows: { place: number; name: string; figure: string; muted?: boolean }[];
  highlightFirst?: boolean;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", marginTop: 26, gap: 10 }}>
      {rows.map((row) => {
        const leader = highlightFirst && row.place === 1;
        return (
          <div
            key={`${row.place}-${row.name}`}
            style={{ display: "flex", alignItems: "center", gap: 18, fontSize: 27 }}
          >
            <div
              style={{
                display: "flex",
                width: 34,
                justifyContent: "flex-end",
                color: leader ? COLOUR.accent : COLOUR.inkFaint,
              }}
            >
              {row.place}
            </div>
            <div
              style={{
                display: "flex",
                flexGrow: 1,
                flexBasis: 0,
                minWidth: 0,
                overflow: "hidden",
                whiteSpace: "nowrap",
                color: row.muted ? COLOUR.inkDim : COLOUR.ink,
              }}
            >
              {clip(row.name, 30)}
            </div>
            <div
              style={{
                display: "flex",
                width: 104,
                flexShrink: 0,
                justifyContent: "flex-end",
                whiteSpace: "nowrap",
                color: leader ? COLOUR.accent : COLOUR.inkDim,
              }}
            >
              {row.figure}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * The fallback every card falls back to.
 *
 * A card whose record has gone — a deleted game, a model that left the catalogue, a mistyped slug
 * — must still be a picture. An unfurler that gets a 404 for the image shows the link with a
 * broken-image box, which looks like the site is down rather than like the page is missing.
 */
export function Missing({ what }: { what: string }) {
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 20,
        background: COLOUR.ground,
      }}
    >
      <Wordmark />
      <div style={{ display: "flex", fontSize: 38, color: COLOUR.inkDim }}>{what}</div>
    </div>
  );
}
