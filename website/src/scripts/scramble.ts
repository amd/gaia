// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The headline resolves out of human history. Letters appear in random places,
// each as a character from an old writing system, and the whole line moves
// through those systems in date order — Ice Age signs to code — as the
// starfield brakes out of warp. Glyphs change at a rate tied to the stars'
// speed, so they flicker fastest at warp and slow as the ship does; the text
// settles as the stars come to rest.
//
// Purely visual: the h1's aria-label carries the sentence. Each real character
// stays in the layout (transparent) while a glyph sits over it, so the line
// never reflows. With reduced motion, or the background paused, the text is
// simply there.

import { WRITING_SYSTEMS, type ScriptGlyph } from '../data/scramble-scripts';
import { ARRIVE_S, CRUISE, WARP, starFieldMounted, warpElapsed, warpSpeed, warpTravelled } from './starfield';

/** Timing and jitter of the scramble. */
export const SCRAMBLE = {
  /** How long a glyph shows at full warp; it lengthens as the stars slow. */
  glyphMs: 72,
  /** The text settles at this share of the braking. */
  settleAt: 0.86,
  /** Share of the settle time over which letters first appear. */
  span: 0.4,
  /** Jitter in each glyph's offset, rotation and scale at warp; it calms with the stars. */
  chaos: 0.18,
  /** How far, in writing systems, a letter may stray from the line's place in history. */
  spread: 0.8,
} as const;

/** A deterministic 0..1 hash of two integers, so a letter's choices are stable within a tick. */
export function hash(a: number, b: number): number {
  let h = (a * 374761393 + b * 668265263) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/** Where the line is in history, as a writing-system index, after travelling `d` of `dEnd`. */
export function eraAt(d: number, dEnd: number, systems: number): number {
  return Math.min(1, Math.max(0, d / dEnd)) * (systems - 1);
}

export interface Pick {
  system: number;
  glyph: number;
  key: string;
}

/**
 * The glyph a letter shows next: from the writing system nearest `target`,
 * never one already on screen (`used`) and never the letter's previous one.
 * Searches outward from `target` when that system's glyphs are all taken.
 */
export function pickGlyph(
  systems: ReadonlyArray<{ id: string; glyphs: ReadonlyArray<unknown> }>,
  target: number,
  used: ReadonlySet<string>,
  previous: string | null,
  seed: number,
): Pick {
  const n = systems.length;
  const start = Math.max(0, Math.min(n - 1, Math.round(target)));
  for (let d = 0; d < n * 2; d++) {
    const s = start + (d % 2 ? (d + 1) / 2 : -d / 2);
    if (s < 0 || s >= n) continue;
    const count = systems[s].glyphs.length;
    for (let j = 0; j < count; j++) {
      const g = (Math.floor(seed * count) + j) % count;
      const key = `${systems[s].id}:${g}`;
      if (!used.has(key) && key !== previous) return { system: s, glyph: g, key };
    }
  }
  throw new Error(`[scramble] no free glyph: ${used.size} on screen across ${n} writing systems.`);
}

/** A glyph as nodes: a drawing for the Ice Age signs, text (never markup) for a character. */
function glyphNode(g: ScriptGlyph): Node {
  if (g.svg) return document.createRange().createContextualFragment(g.svg);
  const span = document.createElement('span');
  span.className = 'g-scr-char';
  span.textContent = g.char ?? '';
  return span;
}

interface Letter {
  slot: HTMLElement;
  overlay: HTMLElement | null;
  index: number;
  phase: number;
  typeAt: number;
  resolveAt: number;
  shown: boolean;
  settled: boolean;
  current: string | null;
  tick: number;
}

/**
 * Runs the scramble over every `[data-decode]` element. Returns a stop
 * function that settles the text at once.
 */
export function mountScramble(lines: HTMLElement[]): () => void {
  if (!starFieldMounted()) {
    throw new Error('[scramble] the starfield must mount first: the headline runs on its warp clock (StarField.astro).');
  }
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  // The field is still (reduced motion, or paused from the footer): the text is simply there.
  if (reduce || warpElapsed() >= ARRIVE_S) return () => {};

  // Start the font now: an unfetched face would leave the first glyphs blank.
  const chars = WRITING_SYSTEMS.flatMap((w) => w.glyphs.map((g) => g.char ?? '')).join('');
  void document.fonts.load("1em 'GAIA Scripts'", chars);

  const seed = Math.floor(Math.random() * 1e6);
  const glyphDistance = WARP * (SCRAMBLE.glyphMs / 1000); // distance travelled per glyph at full warp
  const tEnd = ARRIVE_S * SCRAMBLE.settleAt;
  const dEnd = warpTravelled(tEnd);
  const systems = WRITING_SYSTEMS;

  const letters: Letter[] = [];
  for (const el of lines) {
    const text = el.textContent ?? '';
    el.textContent = '';
    // Letters are grouped by word, so a narrow screen wraps between words and
    // never between letters; the spaces stay text so they keep their width.
    let word: HTMLElement | null = null;
    for (const ch of text) {
      if (ch === ' ') {
        el.appendChild(document.createTextNode(' '));
        word = null;
        continue;
      }
      if (!word) {
        word = document.createElement('span');
        word.className = 'g-scr-word';
        el.appendChild(word);
      }
      const slot = document.createElement('span');
      slot.className = 'g-scr';
      slot.textContent = ch;
      word.appendChild(slot);
      const index = letters.length;
      const typeAt = tEnd * SCRAMBLE.span * hash(index + seed, 99);
      const resolveAt = Math.min(tEnd, typeAt + tEnd * (1 - SCRAMBLE.span) * (0.75 + 0.25 * hash(index + seed, 5)));
      slot.classList.add('is-waiting');
      letters.push({ slot, overlay: null, index, phase: hash(index + seed, 7), typeAt, resolveAt, shown: false, settled: false, current: null, tick: -1 });
    }
  }

  const settle = (l: Letter, animate: boolean) => {
    l.settled = true;
    l.overlay?.remove();
    l.overlay = null;
    l.slot.classList.remove('is-waiting', 'is-scrambling');
    if (animate) {
      l.slot.animate(
        [
          { opacity: 0.35, filter: 'blur(3px)', transform: 'translateY(-0.05em)' },
          { opacity: 1, filter: 'none', transform: 'none' },
        ],
        { duration: 320, easing: 'ease-out' },
      );
    }
  };

  let raf = 0;
  const frame = () => {
    const t = warpElapsed();
    const v = warpSpeed(t);
    const d = warpTravelled(t);
    const tickBase = d / glyphDistance;
    const era = eraAt(d, dEnd, systems.length);
    const chaos = SCRAMBLE.chaos * Math.min(1, ((v - CRUISE) / (WARP - CRUISE)) * 1.6);

    const live: Letter[] = [];
    for (const l of letters) {
      if (l.settled || t < l.typeAt) continue;
      if (t >= l.resolveAt) {
        settle(l, true);
        continue;
      }
      if (!l.shown) {
        l.shown = true;
        l.slot.classList.replace('is-waiting', 'is-scrambling');
        l.overlay = document.createElement('span');
        l.overlay.className = 'g-scr-glyph';
        l.slot.appendChild(l.overlay);
        l.overlay.animate(
          [
            { transform: 'scale(4)', opacity: 0, filter: 'blur(6px)' },
            { transform: 'none', opacity: 1, filter: 'none' },
          ],
          { duration: 260, easing: 'cubic-bezier(.2,.8,.2,1)' },
        );
      }
      live.push(l);
    }

    // Every glyph on screen is unique, and a letter never repeats the glyph it just showed.
    const used = new Set<string>();
    for (const l of live) if (l.current && l.tick === Math.floor(tickBase + l.phase)) used.add(l.current);
    for (const l of live) {
      const tick = Math.floor(tickBase + l.phase);
      if (l.current && l.tick === tick) continue;
      const target = era + (hash(l.index + seed, tick) * 2 - 1) * SCRAMBLE.spread;
      const pick = pickGlyph(systems, target, used, l.current, hash(l.index * 31 + 3 + seed, tick));
      used.add(pick.key);
      l.current = pick.key;
      l.tick = tick;
      const r = hash(l.index + seed, tick + 1) * 2 - 1;
      const q = hash(l.index + seed, tick + 2) * 2 - 1;
      const inner = document.createElement('span');
      inner.className = 'g-scr-inner';
      if (chaos > 0) {
        inner.style.transform = `translate(${(q * chaos * 0.18).toFixed(3)}em, ${(r * chaos * 0.14).toFixed(3)}em) rotate(${(r * chaos * 28).toFixed(1)}deg) scale(${(1 + q * chaos * 0.35).toFixed(2)})`;
      }
      inner.replaceChildren(glyphNode(systems[pick.system].glyphs[pick.glyph]));
      l.overlay?.replaceChildren(inner);
    }

    if (letters.some((l) => !l.settled)) raf = requestAnimationFrame(frame);
  };

  const stop = () => {
    cancelAnimationFrame(raf);
    for (const l of letters) if (!l.settled) settle(l, false);
  };
  // Paused from the footer mid-scramble: the warp clock stops, so finish now.
  const onMotion = () => stop();
  document.addEventListener('gaia-motion', onMotion, { once: true });

  raf = requestAnimationFrame(frame);
  return () => {
    document.removeEventListener('gaia-motion', onMotion);
    stop();
  };
}
