"use client";

/**
 * A replay board that plays itself (Phase 19).
 *
 * The row used to show three final positions — a photograph of a game rather than a game. Live
 * games are the exception on a small deployment, so the front page has to carry its own motion.
 *
 * Draws through the same `Board` the game page uses. The first version drew its own grid of
 * Unicode glyphs, which was cheap and unreliable: the positions were correct but the pieces
 * rendered differently depending on which system font won, and broke up as games progressed.
 *
 * No new data: `GameDetail.moves` is already fetched for each card, so this is a rendering change.
 * Every position is derived once, up front, by replaying SAN through chess.js; the animation is
 * then an index into that array. Recomputing the position on each tick would replay the whole
 * game every 750ms, three times over.
 *
 * The decisions about *whether* to run — reduced motion, visibility — live in `lib/animation`
 * where they can be asserted without a DOM.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { Board } from "@/components/Board";
import {
  PLY_ANIMATION_MS,
  PLY_INTERVAL_MS,
  STAGGER_MS,
  advance,
  buildFrames,
  isContiguous,
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

  /* `animate` travels with `cycle` rather than being derived from the previous render. Deriving
     it needed the last rendered ply, and the only places to keep that are a ref — which cannot be
     read during render — or a second state update per tick, which would re-render mid-slide with
     the animation disabled and cancel it. Computing it where the step is decided avoids both. */
  const [step, setStep] = useState(() => ({
    cycle: staggeredStart(index, plies, count),
    animate: false,
  }));

  /* `null` until the media query is read. Unknown counts as reduced: no timer starts before we
     know, so a reduced-motion visitor never sees a frame of movement. Crucially the unknown state
     renders the *same* frame as the animating one, so resolving it is not a jump. */
  const [reducedMotion, setReducedMotion] = useState<boolean | null>(null);
  const [visible, setVisible] = useState(false);
  const hostRef = useRef<HTMLDivElement | null>(null);

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

  const animating = shouldAnimate({
    reducedMotion: reducedMotion !== false,
    visible,
    plies,
  });

  useEffect(() => {
    if (!animating) return;

    let interval: ReturnType<typeof setInterval> | undefined;

    /* Same interval for every board, but a staggered first tick. Identical timers starting
       together look mechanical even when the positions differ. */
    const lead = setTimeout(() => {
      interval = setInterval(() => {
        setStep(({ cycle }) => {
          const next = advance(cycle, plies);
          return {
            cycle: next,
            animate: isContiguous(positionAt(cycle, plies), positionAt(next, plies)),
          };
        });
      }, PLY_INTERVAL_MS);
    }, index * STAGGER_MS);

    return () => {
      clearTimeout(lead);
      if (interval) clearInterval(interval);
    };
  }, [animating, plies, index]);

  /* Reduced motion shows the finished game, which is what the card is advertising. */
  const shown = reducedMotion === true ? plies : positionAt(step.cycle, plies);
  const frame = frames[shown] ?? frames[0];

  return (
    <div
      ref={hostRef}
      data-animating={animating ? "true" : "false"}
      data-ply={shown}
      role="img"
      aria-label={label}
    >
      {/* Notation off at this size, where rank and file labels are unreadable clutter. The slide
          is skipped for anything that is not a single ply — see `isContiguous`. */}
      <Board
        fen={frame.fen}
        lastMove={frame.lastMove}
        showNotation={false}
        animationMs={step.animate ? PLY_ANIMATION_MS : 0}
      />
    </div>
  );
}
