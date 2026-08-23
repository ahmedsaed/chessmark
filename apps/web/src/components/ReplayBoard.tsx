"use client";

/**
 * A replay board that plays itself (Phase 19).
 *
 * The row used to show three final positions — a photograph of a game rather than a game. Live
 * games are the exception on a small deployment, so the front page has to carry its own motion.
 *
 * Draws through the same `Board` the game page uses. The first version drew its own grid of
 * Unicode glyphs, which was cheap and unreliable: the positions were correct but the pieces
 * rendered differently depending on which system font won, and broke up as the games progressed.
 *
 * No new data: `GameDetail.moves` is already fetched for each card, so this is a rendering change.
 * Every position is derived once, up front, by replaying SAN through chess.js; the animation is
 * then an index into that array. Recomputing the position on each tick would replay the whole
 * game every 750ms, three times over, for no benefit.
 *
 * The decisions about *whether* to run — reduced motion, visibility — live in `lib/animation`
 * where they can be asserted without a DOM.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { Board } from "@/components/Board";
import {
  PLY_INTERVAL_MS,
  STAGGER_MS,
  advance,
  buildFrames,
  positionAt,
  shouldAnimate,
  staggeredStart,
} from "@/lib/animation";

export function ReplayBoard({
  startFen,
  moves,
  index,
  count,
  label,
}: {
  startFen: string;
  moves: string[];
  /** Position in the row, used to stagger both the starting ply and the first tick. */
  index: number;
  count: number;
  label?: string;
}) {
  const frames = useMemo(() => buildFrames(startFen, moves), [startFen, moves]);
  const plies = frames.length - 1;

  const [cycle, setCycle] = useState(() => staggeredStart(index, plies, count));
  const [reducedMotion, setReducedMotion] = useState(true);
  const [visible, setVisible] = useState(false);
  const hostRef = useRef<HTMLDivElement | null>(null);

  /* Starts pessimistic — `true` until the media query says otherwise — so the server-rendered
     markup and the first client render agree, and a reduced-motion visitor never sees a frame of
     movement before the check lands. */
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReducedMotion(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry?.isIntersecting ?? false),
      { rootMargin: "96px" },
    );
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  const animating = shouldAnimate({ reducedMotion, visible, plies });

  useEffect(() => {
    if (!animating) return;

    let interval: ReturnType<typeof setInterval> | undefined;

    /* Same interval for every board, but a staggered first tick. Identical timers starting
       together look mechanical even when the positions differ. */
    const lead = setTimeout(() => {
      interval = setInterval(() => setCycle((current) => advance(current, plies)), PLY_INTERVAL_MS);
    }, index * STAGGER_MS);

    return () => {
      clearTimeout(lead);
      if (interval) clearInterval(interval);
    };
  }, [animating, plies, index]);

  /* Reduced motion shows the finished game, which is what the card is advertising. */
  const frame = frames[reducedMotion ? plies : positionAt(cycle, plies)] ?? frames[0];

  return (
    <div
      ref={hostRef}
      data-animating={animating ? "true" : "false"}
      role="img"
      aria-label={label}
    >
      {/* Notation off and the animation kept well under `PLY_INTERVAL_MS`, or moves queue up
          behind the slide and the board falls behind the ply it claims to show. */}
      <Board fen={frame.fen} lastMove={frame.lastMove} showNotation={false} animationMs={280} />
    </div>
  );
}
