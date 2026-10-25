// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { readFileSync } from 'node:fs';
import { brotliDecompressSync } from 'node:zlib';

import { describe, expect, it } from 'vitest';

import { WRITING_SYSTEMS } from '../data/scramble-scripts';
import { SCRAMBLE, eraAt, hash, pickGlyph } from './scramble';
import { ARRIVE_S, CRUISE, WARP, warpSpeed, warpTravelled } from './starfield';

describe('the writing systems', () => {
  it('run oldest first, from Ice Age signs to code', () => {
    const years = WRITING_SYSTEMS.map((w) => w.year);
    expect(years).toEqual([...years].sort((a, b) => a - b));
    expect(WRITING_SYSTEMS[0].id).toBe('ice');
    expect(WRITING_SYSTEMS.at(-1)?.id).toBe('code');
  });

  it('give every glyph exactly one of a character or a drawing, and a meaning', () => {
    for (const w of WRITING_SYSTEMS) {
      expect(w.glyphs.length, w.id).toBeGreaterThan(0);
      for (const g of w.glyphs) {
        expect(Boolean(g.char) !== Boolean(g.svg), `${w.id}: ${g.means}`).toBe(true);
        expect(g.means.length).toBeGreaterThan(0);
        if (g.char) expect([...g.char]).toHaveLength(1);
      }
    }
  });

  it('use unique ids', () => {
    const ids = WRITING_SYSTEMS.map((w) => w.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

// ------------------------------------------------- the shipped font covers them

/** The code points a woff2 maps, read from its cmap table (WOFF2 spec section 5). */
function woff2CodePoints(bytes: Buffer): Set<number> {
  expect(bytes.toString('latin1', 0, 4)).toBe('wOF2');
  const numTables = bytes.readUInt16BE(12);
  const compressed = bytes.readUInt32BE(20);
  let p = 48;
  const base128 = () => {
    let v = 0;
    for (let i = 0; i < 5; i++) {
      const b = bytes[p++];
      v = v * 128 + (b & 0x7f);
      if (!(b & 0x80)) return v;
    }
    throw new Error('bad UIntBase128');
  };
  const tables: Array<{ known: number; length: number }> = [];
  for (let t = 0; t < numTables; t++) {
    const flags = bytes[p++];
    const known = flags & 0x3f;
    if (known === 63) p += 4;
    const version = flags >> 6;
    const orig = base128();
    // glyf (10) and loca (11) are transformed at version 0; every other table at a non-zero one.
    const transformed = known === 10 || known === 11 ? version === 0 : version !== 0;
    tables.push({ known, length: transformed ? base128() : orig });
  }
  const data = brotliDecompressSync(bytes.subarray(p, p + compressed));
  let off = 0;
  let cmap = -1;
  for (const t of tables) {
    if (t.known === 0) cmap = off;
    off += t.length;
  }
  expect(cmap).toBeGreaterThanOrEqual(0);
  const points = new Set<number>();
  const n = data.readUInt16BE(cmap + 2);
  for (let i = 0; i < n; i++) {
    const rec = cmap + 4 + i * 8;
    const sub = cmap + data.readUInt32BE(rec + 4);
    if (data.readUInt16BE(sub) !== 12) continue;
    const groups = data.readUInt32BE(sub + 12);
    for (let g = 0; g < groups; g++) {
      const start = data.readUInt32BE(sub + 16 + g * 12);
      const end = data.readUInt32BE(sub + 20 + g * 12);
      for (let c = start; c <= end; c++) points.add(c);
    }
  }
  return points;
}

describe('public/fonts/gaia-scripts.woff2', () => {
  const points = woff2CodePoints(readFileSync(new URL('../../public/fonts/gaia-scripts.woff2', import.meta.url)));

  // A character the font lacks renders as a missing-glyph box in the headline.
  // Fix: python tools/subset-scripts-font.py
  it('holds every non-ASCII character the scramble can show', () => {
    expect(points.size).toBeGreaterThan(90);
    const missing = WRITING_SYSTEMS.flatMap((w) => w.glyphs.map((g) => g.char ?? ''))
      .filter((c) => c && c.codePointAt(0)! > 0x7f && !points.has(c.codePointAt(0)!));
    expect(missing).toEqual([]);
  });
});

// ---------------------------------------------------------------- the engine

describe('hash', () => {
  it('stays in [0, 1) and is stable', () => {
    for (let i = 0; i < 500; i++) {
      const h = hash(i, i * 7);
      expect(h).toBeGreaterThanOrEqual(0);
      expect(h).toBeLessThan(1);
      expect(hash(i, i * 7)).toBe(h);
    }
  });
});

describe('eraAt', () => {
  it('moves from the oldest system to the newest, never back, and stops at the newest', () => {
    const n = WRITING_SYSTEMS.length;
    const dEnd = warpTravelled(ARRIVE_S * SCRAMBLE.settleAt);
    let prev = -1;
    for (let t = 0; t <= ARRIVE_S; t += 0.01) {
      const e = eraAt(warpTravelled(t), dEnd, n);
      expect(e).toBeGreaterThanOrEqual(prev);
      prev = e;
    }
    expect(eraAt(0, dEnd, n)).toBe(0);
    expect(prev).toBe(n - 1);
  });
});

describe('pickGlyph', () => {
  it('never shows the same glyph twice at once, nor a letter its previous glyph', () => {
    // 23 letters on screen, the headline's count, all aiming at the same era, over 50 ticks.
    const previous: Array<string | null> = Array(23).fill(null);
    for (let tick = 0; tick < 50; tick++) {
      const used = new Set<string>();
      previous.forEach((prev, letter) => {
        const p = pickGlyph(WRITING_SYSTEMS, 3, used, prev, hash(letter, tick));
        expect(used.has(p.key)).toBe(false);
        expect(p.key).not.toBe(prev);
        used.add(p.key);
        previous[letter] = p.key;
      });
    }
  });

  it('prefers the target system and strays only as far as it must', () => {
    const p = pickGlyph(WRITING_SYSTEMS, 6, new Set(), null, 0);
    expect(p.system).toBe(6);
  });

  it('fails loudly when there is no free glyph left', () => {
    const tiny = [{ id: 'a', glyphs: [1] }];
    expect(() => pickGlyph(tiny, 0, new Set(['a:0']), null, 0)).toThrow(/no free glyph/);
  });
});

describe('the warp clock the headline shares with the starfield', () => {
  it('brakes from WARP to CRUISE over ARRIVE_S', () => {
    expect(warpSpeed(0)).toBe(WARP);
    expect(warpSpeed(ARRIVE_S)).toBe(CRUISE);
    expect(warpSpeed(ARRIVE_S + 5)).toBe(CRUISE);
  });

  it('measures distance as the integral of speed', () => {
    let d = 0;
    const dt = 0.0005;
    for (let t = 0; t < ARRIVE_S; t += dt) d += warpSpeed(t + dt / 2) * dt;
    expect(warpTravelled(ARRIVE_S)).toBeCloseTo(d, 2);
  });
});
