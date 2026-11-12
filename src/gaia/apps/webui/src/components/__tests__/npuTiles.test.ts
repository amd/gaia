// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { DISSOLVE_S, TILE_PITCH, TileField } from '../npuTiles';

const W = 600;
const H = 300;
const HOLE = { cx: 300, cy: 150, rx: 100, ry: 60 };

function field(): TileField {
    const f = new TileField();
    f.resize(W, H, HOLE);
    return f;
}

/** The tile whose centre is nearest (x, y). */
function tileAt(f: TileField, x: number, y: number): [number, number] {
    let best: [number, number] = [0, 0];
    let bestD = Infinity;
    for (let r = 0; r < f.rows; r++) {
        for (let c = 0; c < f.cols; c++) {
            const [cx, cy] = f.center(c, r);
            const d = Math.hypot(cx - x, cy - y);
            if (d < bestD) { bestD = d; best = [c, r]; }
        }
    }
    return best;
}

function run(f: TileField, seconds: number): void {
    for (let t = 0; t < seconds; t += 1 / 30) f.step(1 / 30);
}

function maxEnergy(f: TileField): number {
    let m = 0;
    for (let r = 0; r < f.rows; r++) for (let c = 0; c < f.cols; c++) m = Math.max(m, f.energyAt(c, r));
    return m;
}

/** A context that records what gets painted, and with what. */
function recorder() {
    const paints: Array<{ op: string; style: string }> = [];
    const ctx = new Proxy({} as Record<PropertyKey, unknown>, {
        get: (s, k) => {
            if (k === 'fillRect' || k === 'fill') return () => { if (s.globalCompositeOperation !== 'destination-out') paints.push({ op: String(k), style: String(s.fillStyle) }); };
            return k in s ? s[k] : () => undefined;
        },
        set: (s, k, v) => { s[k] = v; return true; },
    }) as unknown as CanvasRenderingContext2D;
    return { ctx, paints };
}

const PALETTE = { fg: '#111111', accent: '#B08D57', dark: false };

describe('TileField', () => {
    it('covers the canvas with one dot per pitch', () => {
        const f = field();
        expect(f.cols).toBe(Math.ceil(W / TILE_PITCH));
        expect(f.rows).toBe(Math.ceil(H / TILE_PITCH));
    });

    it('starts at rest, so a still frame shows only the dots', () => {
        const f = field();
        expect(maxEnergy(f)).toBe(0);
        const { ctx, paints } = recorder();
        f.draw(ctx, PALETTE);
        expect(paints.every((p) => p.style === PALETTE.fg)).toBe(true);
    });

    it('wakes the dots under the pointer and lets them settle once it leaves', () => {
        const f = field();
        f.setPointer(90, 90, false);
        run(f, 0.3);
        expect(f.energyAt(...tileAt(f, 90, 90))).toBeGreaterThan(0.2);
        expect(f.energyAt(...tileAt(f, 540, 270))).toBe(0);

        f.clearPointer();
        run(f, 1.5);
        expect(f.energyAt(...tileAt(f, 90, 90))).toBeLessThan(0.05);
    });

    it('pushes nearby dots away from the pointer and springs them back after', () => {
        const f = field();
        const [c, r] = tileAt(f, 120, 90);
        const [cx] = f.center(c, r);
        f.setPointer(cx - 30, f.center(c, r)[1], false);
        run(f, 0.5);
        expect(f.offsetAt(c, r)[0]).toBeGreaterThan(2);

        f.clearPointer();
        run(f, 1);
        expect(Math.abs(f.offsetAt(c, r)[0])).toBeLessThan(0.1);
    });

    it('reaches further when the pointer moves fast', () => {
        const slow = field();
        const fast = field();
        slow.setPointer(90, 90, false, 0);
        fast.setPointer(90, 90, false, 1500);
        run(slow, 0.3);
        run(fast, 0.3);
        const [c, r] = tileAt(slow, 90 + 75, 90);
        expect(fast.energyAt(c, r)).toBeGreaterThan(slow.energyAt(c, r));
    });

    it('sends a ripple outward as a ring, leaving the far side untouched until it arrives', () => {
        const f = field();
        f.ripple(300, 150, 1);
        run(f, 0.3);
        expect(maxEnergy(f)).toBeGreaterThan(0.3);
        expect(f.energyAt(...tileAt(f, 15, 15))).toBe(0);
    });

    it('makes a keystroke ripple fainter than a send', () => {
        const key = field();
        const send = field();
        key.ripple(300, 150, 0.45);
        send.ripple(300, 150, 1);
        run(key, 0.3);
        run(send, 0.3);
        expect(maxEnergy(key)).toBeLessThan(maxEnergy(send));
    });

    it('sweeps from the left while a model loads', () => {
        const f = field();
        f.setActivity('loading');
        run(f, 0.6);
        const left = Math.max(...Array.from({ length: f.rows }, (_, r) => f.energyAt(0, r)));
        expect(left).toBeGreaterThan(0.1);
        expect(f.energyAt(f.cols - 1, f.rows - 1)).toBe(0);
    });

    it('fills from the bottom up as a download progresses', () => {
        const f = field();
        f.setActivity('download');
        f.setProgress(0.5);
        run(f, 0.5);
        expect(f.energyAt(3, f.rows - 1)).toBeGreaterThan(0.1);
        expect(f.energyAt(3, 0)).toBe(0);

        // Done: the fill drains (an idle wave may still cross part of the row).
        f.setActivity('idle');
        run(f, 2);
        const bottom = Array.from({ length: f.cols }, (_, c) => f.energyAt(c, f.rows - 1));
        expect(bottom.reduce((a, b) => a + b, 0) / bottom.length).toBeLessThan(0.08);
    });

    it('dissolves on send, then draws nothing until restored', () => {
        const f = field();
        f.setActivity('sending');
        f.dissolve(300, 250);
        run(f, DISSOLVE_S / 2);
        expect(f.cleared).toBe(false);
        run(f, DISSOLVE_S);
        expect(f.cleared).toBe(true);

        const { ctx, paints } = recorder();
        f.draw(ctx, PALETTE);
        expect(paints).toHaveLength(0);

        f.restore();
        expect(f.cleared).toBe(false);
        f.draw(ctx, PALETTE);
        expect(paints.length).toBeGreaterThan(0);
    });

    it('comes back when a send fails mid-dissolve', () => {
        const f = field();
        f.dissolve(300, 250);
        run(f, DISSOLVE_S / 3);
        expect(f.dissolving).toBe(true);
        expect(f.cleared).toBe(false);

        f.restore();
        expect(f.dissolving).toBe(false);
        run(f, DISSOLVE_S * 2);
        expect(f.cleared).toBe(false);
    });

    it('does no work while everything is at rest between idle waves', () => {
        const f = field();
        run(f, 0.5);
        // Settled and the first idle wave not yet due: frames can be skipped.
        expect(f.step(1 / 30)).toBe(false);
        f.setPointer(90, 90, false);
        expect(f.step(1 / 30)).toBe(true);
    });

    it('paints a dot at work in the accent', () => {
        const f = field();
        f.setPointer(90, 90, false);
        run(f, 0.3);
        const { ctx, paints } = recorder();
        f.draw(ctx, PALETTE);
        expect(paints.some((p) => p.style === PALETTE.accent)).toBe(true);
    });
});
