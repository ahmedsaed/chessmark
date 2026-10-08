# 0065. Game GIFs are drawn in the browser

**Status:** Accepted
**Date:** 2026-10-08

## Context

A game page gives you a link and a PGN. Neither is something you can drop into a chat and have it
*show* the game. A GIF does that: one frame per ply, playing back the game.

The GIF could be made in two places:

- **On the server**, as `GET /games/{id}/gif` beside `/pgn`. That gives it a stable URL, but the
  API would need Pillow and an SVG rasterizer, and every export would cost worker CPU.
- **In the browser**, on the click. The site already has the pieces as SVG (`lib/og/pieces.ts`) and
  the board's colours, so a canvas can draw exactly what the page shows.

## Decision

**The browser draws and encodes the GIF, in a Web Worker.** `lib/gif/frames.ts` replays the moves
into one frame per ply and holds the rules: the timings, the orientation and the captions.
`lib/gif/draw.ts` paints each frame on a canvas. `lib/gif/encode.ts` encodes the frames with
[`gifenc`](https://github.com/mattdesl/gifenc) (MIT, no dependencies).

- **Off the main thread.** `lib/gif/export.ts` decodes the twelve piece SVGs into `ImageBitmap`s,
  since a worker has no `Image`. It hands them to `gif.worker.ts`, which draws every frame on an
  `OffscreenCanvas` and encodes it there. In the page, exporting the 447-ply game was one
  1.6-second freeze. In the worker it makes no main-thread task over 50 ms. A browser without
  `OffscreenCanvas` (Safari before 16.4) runs the same `renderGif` in the page and gets the same
  GIF; only that browser pauses.
- **Loaded only on click.** The button `import()`s the encoder, and the worker is a chunk of its
  own, so the game page's bundle does not grow for readers who never use it. The encoder's chunk
  is about 9 KB gzipped.
- **Fresh data.** The button fetches `/games/{id}` when it is pressed, so a GIF of a live game
  shows the moves made so far.
- **Only changed pixels are written.** One palette is shared by every frame. Pixels that are
  unchanged from the previous frame are transparent and leave the previous frame in place. A
  447-ply game comes to 1.3 MB this way, against 6.2 MB with every frame written whole.
- **No reasoning.** The GIF shows only the moves, the names and the result, so invariant 8 is not
  involved.

The same change turned the action row (sound, copy link, PGN, GIF) into icons. Each icon has an
`aria-label` and a `title`. A fourth labelled word would have made the row wider than the status
row it sits in can spare.

## Alternatives considered

**A server endpoint.** Its one advantage is a URL people could link to or embed. It loses on cost:
a new image stack in the API and CPU on every export. Nobody needs the URL yet. If someone does,
this decision is the one to revisit.

## Consequences

- One new frontend dependency. `gifenc` has not changed since 2022 and ships no types, so
  `src/types/gifenc.d.ts` declares only the functions we call.
- The canvas code cannot be unit-tested, because Node has no canvas. It is excluded from coverage.
  `e2e/public/gif.spec.ts` exports a real game, composites the frames back together, and checks
  the final position square by square.
- The palette has one slot reserved for "unchanged". Pixels must be matched against the palette
  *without* that slot. When they were not, black pieces turned transparent as they moved.
  `encode.ts` explains this, and the unit suite has a test that fails if it happens again.
- Drawing code must stay free of the DOM, because the worker has none. `draw.ts` takes the canvas
  and the pieces it draws with as arguments, and does not create them itself.
