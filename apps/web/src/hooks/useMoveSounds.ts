"use client";

import { useEffect, useSyncExternalStore } from "react";

import { SOUND_NAMES, type SoundName } from "@/lib/sound";

/**
 * Move sounds: the preference, the preload, and the playback.
 *
 * **Web Audio rather than `<audio>` elements.** An element per sound cannot overlap itself, so a
 * reconnect delivering two plies inside 300ms would cut the first one off; and
 * `HTMLMediaElement.play()` has tens of milliseconds of start latency, which is audible against a
 * piece that has already landed. A decoded `AudioBuffer` starts on the next audio quantum and can
 * be played any number of times at once.
 *
 * **Fetched early, decoded late.** The bytes are fetched once the browser is idle after the game
 * page loads — never on the critical path, and nothing renders differently while they are in
 * flight. They are *decoded* only on the visitor's first click or key press, because that is the
 * earliest an `AudioContext` is allowed to run: browsers refuse sound before a gesture on the
 * page, and a context created earlier starts suspended and logs a warning in Chrome. So "on by
 * default" means on from the first interaction, which is also how chess.com and lichess behave.
 *
 * The files are CC0, from Kenney's *Impact Sounds* and *Interface Sounds* packs (kenney.nl). The
 * project is source-available, which rules out lichess's sets (non-free, AGPL or non-commercial);
 * the provenance and how each file was mixed is in FRONTEND.md.
 */

/**
 * Versioned, because the files are served `immutable` (next.config.ts) — a changed sound under the
 * same URL would never reach a browser that already has the old one. A new mix is a new directory.
 */
const SOUND_BASE = "/sounds/v1";

/** Below full scale: the files are peak-limited near 0 dB, and a board is not an alarm. */
const GAIN = 0.6;

const STORAGE_KEY = "chessmark:sound";

/* ---------------------------------------------------------------- preference ------------------ */

/**
 * On unless this visitor has turned it off. Read through `useSyncExternalStore`, so every toggle
 * and every board on the page agree, and a second tab hears about the change through `storage`.
 */
const listeners = new Set<() => void>();

/** A choice storage could not hold. Wins over storage for the life of the page. */
let override: boolean | null = null;

export function soundEnabledSnapshot(): boolean {
  if (override !== null) return override;
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== "off";
  } catch {
    // Storage can throw outright (Safari private mode, blocked site data). The default stands.
    return true;
  }
}

/** What the server renders: the default. A visitor who turned it off is corrected on hydration. */
export function soundEnabledOnServer(): boolean {
  return true;
}

export function setSoundEnabled(enabled: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, enabled ? "on" : "off");
    /* Cleared on success, not set: an override that outlived a working write would make this tab
       deaf to a change made in another one. */
    override = null;
  } catch {
    // Not persisted, but the toggle must still work for this page.
    override = enabled;
  }
  listeners.forEach((listener) => listener());
  // Turning sound on is a click, which is exactly the gesture that may start the context.
  if (enabled) unlock();
}

export function subscribeSoundEnabled(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/* ---------------------------------------------------------------- playback -------------------- */

let context: AudioContext | null = null;
let fetched: Promise<Map<SoundName, ArrayBuffer>> | null = null;
let decoded: Promise<Map<SoundName, AudioBuffer>> | null = null;

/** Starts the downloads. Idempotent: every board on a page may ask; one set of requests is made. */
export function preloadSounds(): void {
  if (fetched || typeof window === "undefined") return;
  fetched = Promise.all(
    SOUND_NAMES.map(async (name) => {
      try {
        const response = await fetch(`${SOUND_BASE}/${name}.mp3`);
        return response.ok ? ([name, await response.arrayBuffer()] as const) : null;
      } catch {
        return null;
      }
    }),
  ).then((entries) => new Map(entries.filter((entry) => entry !== null)));
}

/**
 * Creates or resumes the context. Called from a gesture handler, and only works from one.
 *
 * Decoding happens here, once, the first time it is possible. A missing or undecodable file is
 * skipped rather than failing the set: one silent sound is better than none at all.
 */
export function unlock(): void {
  if (typeof window === "undefined" || !soundEnabledSnapshot()) return;
  if (!context) {
    const Context = window.AudioContext ?? (window as { webkitAudioContext?: typeof AudioContext })
      .webkitAudioContext;
    if (!Context) return;
    context = new Context();
  }
  if (context.state === "suspended") void context.resume().catch(() => undefined);

  if (!decoded) {
    preloadSounds();
    const ctx = context;
    decoded = (fetched ?? Promise.resolve(new Map<SoundName, ArrayBuffer>())).then(
      async (raw) => {
        const buffers = new Map<SoundName, AudioBuffer>();
        await Promise.all(
          [...raw].map(async ([name, bytes]) => {
            try {
              // `slice`: decoding detaches the buffer it is given, and a failed decode must not
              // leave the cached bytes unusable for a retry.
              buffers.set(name, await ctx.decodeAudioData(bytes.slice(0)));
            } catch {
              // Skipped; that one sound stays silent.
            }
          }),
        );
        return buffers;
      },
    );
  }
}

/**
 * Plays a sound if the visitor has sound on and the page has been interacted with.
 *
 * Before the first gesture this does nothing at all — it does not queue. A move that landed while
 * the context was locked is in the past by the time it unlocks, and hearing it late is worse than
 * not hearing it.
 */
export function playSound(name: SoundName): void {
  if (!context || context.state !== "running" || !decoded || !soundEnabledSnapshot()) return;
  const ctx = context;
  void decoded.then((buffers) => {
    const buffer = buffers.get(name);
    if (!buffer) return;
    const source = ctx.createBufferSource();
    const gain = ctx.createGain();
    gain.gain.value = GAIN;
    source.buffer = buffer;
    source.connect(gain).connect(ctx.destination);
    source.start();
  });
}

/* ---------------------------------------------------------------- hooks ----------------------- */

/**
 * Readies move sounds for a board that makes them: the live game and the replay.
 *
 * Fetching waits for the browser to be idle, so six small files never compete with the page's own
 * requests. The first click or key press anywhere on the page is what unlocks audio — not a
 * prompt, not a button: browsers allow sound after any gesture, and the visitor has usually made
 * one by the time a move lands.
 */
export function useMoveSounds(): void {
  useEffect(() => {
    const idle = window.requestIdleCallback ?? ((callback: () => void) => setTimeout(callback, 1));
    const cancelIdle = window.cancelIdleCallback ?? clearTimeout;
    const handle = idle(() => preloadSounds());

    const onGesture = () => unlock();
    const events = ["pointerdown", "keydown"] as const;
    events.forEach((event) => window.addEventListener(event, onGesture, { passive: true }));

    return () => {
      cancelIdle(handle as number);
      events.forEach((event) => window.removeEventListener(event, onGesture));
    };
  }, []);
}

/** The visitor's sound preference, and the setter. Every caller on a page sees the same value. */
export function useSoundEnabled(): [boolean, (enabled: boolean) => void] {
  const enabled = useSyncExternalStore(
    subscribeSoundEnabled,
    soundEnabledSnapshot,
    soundEnabledOnServer,
  );
  return [enabled, setSoundEnabled];
}
