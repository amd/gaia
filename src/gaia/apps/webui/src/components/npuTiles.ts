// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The new-chat backdrop: a field of dots laid out like a Ryzen AI NPU's tile
 * array. A dot at work swells into a tile; dots shy away from the pointer and
 * are pushed by every ripple before springing back. What moves says what GAIA
 * is doing: a rare slow wave when idle, sweeps while a model loads, the array
 * filling bottom-up during a download, a ripple from the composer per
 * keystroke, and on send the field dissolves outward and stops drawing.
 * Framework-free so it can be stepped and drawn in tests.
 */

export type TileActivity = 'idle' | 'loading' | 'download' | 'sending';

export interface TilePalette {
    fg: string;
    accent: string;
    dark: boolean;
}

/** The content box the field dims behind, in canvas pixels. */
export interface TileHole {
    cx: number;
    cy: number;
    rx: number;
    ry: number;
}

interface Wave { front: number; speed: number; dir: 1 | -1; span: number; strength: number }
interface Ripple { x: number; y: number; r: number; t: number; life: number; strength: number; speed: number; phase: number }
interface Exit { x: number; y: number; t: number; reach: number; phase: number }

export const TILE_PITCH = 30;
/** How long the send dissolve takes, in seconds. */
export const DISSOLVE_S = 1.1;
const POINTER_REACH = 60;
const PUSH_REACH = 110;
const RIPPLE_BAND = 24;
const DECAY_PER_S = 2.4;

const TAU = Math.PI * 2;
const smooth = (e0: number, e1: number, x: number) => {
    const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
    return t * t * (3 - 2 * t);
};

/** Deterministic, so a still frame and a test run draw the same field. */
function mulberry32(seed: number): () => number {
    let a = seed;
    return () => {
        a = (a + 0x6d2b79f5) | 0;
        let t = Math.imul(a ^ (a >>> 15), 1 | a);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

export class TileField {
    cols = 0;
    rows = 0;
    private w = 0;
    private h = 0;
    private ox = 0;
    private oy = 0;
    private time = 0;
    private energy = new Float32Array(0);
    private offset = new Float32Array(0);
    private mask = new Float32Array(0);
    private phase = new Float32Array(0);
    private waves: Wave[] = [];
    private ripples: Ripple[] = [];
    private waveIn = 1.5;
    private dragIn = 0;
    private activity: TileActivity = 'idle';
    private progress = 0;
    private pointer: { x: number; y: number; down: boolean; speed: number } | null = null;
    private exit: Exit | null = null;
    private done = false;
    private settled = false;
    private rand: () => number;

    constructor(seed = 7) {
        this.rand = mulberry32(seed);
    }

    /** True once a send has dissolved the field; nothing more is drawn until `restore`. */
    get cleared(): boolean {
        return this.done;
    }

    /** A send's dissolve is running or has finished. */
    get dissolving(): boolean {
        return this.exit !== null || this.done;
    }

    resize(w: number, h: number, hole: TileHole): void {
        this.w = w;
        this.h = h;
        this.cols = Math.ceil(w / TILE_PITCH);
        this.rows = Math.ceil(h / TILE_PITCH);
        this.ox = (w - this.cols * TILE_PITCH) / 2;
        this.oy = (h - this.rows * TILE_PITCH) / 2;
        const n = this.cols * this.rows;
        this.energy = new Float32Array(n);
        this.offset = new Float32Array(n * 2);
        this.phase = Float32Array.from({ length: n }, () => this.rand() * TAU);
        this.setHole(hole);
    }

    /** Quiet behind the greeting and composer, fading out at the top and bottom edges. */
    setHole(hole: TileHole): void {
        this.mask = new Float32Array(this.cols * this.rows);
        for (let r = 0; r < this.rows; r++) {
            for (let c = 0; c < this.cols; c++) {
                const [x, y] = this.center(c, r);
                const d = Math.hypot((x - hole.cx) / hole.rx, (y - hole.cy) / hole.ry);
                this.mask[r * this.cols + c] = (0.3 + 0.7 * smooth(0.75, 1.5, d)) * smooth(0, 50, y) * smooth(0, 50, this.h - y);
            }
        }
    }

    setActivity(activity: TileActivity): void {
        if (activity === this.activity) return;
        this.activity = activity;
        this.waveIn = 0;
    }

    /** Download progress, 0 to 1; only drawn while the activity is `download`. */
    setProgress(progress: number): void {
        this.progress = Math.min(1, Math.max(0, progress));
    }

    setPointer(x: number, y: number, down: boolean, speed = 0): void {
        this.pointer = { x, y, down, speed };
    }

    clearPointer(): void {
        this.pointer = null;
    }

    /** A ring of work spreading out from (x, y); 1 is a send, ~0.45 a keystroke. */
    ripple(x: number, y: number, strength: number): void {
        if (this.ripples.length > 24) this.ripples.shift();
        this.ripples.push({ x, y, r: 0, t: 0, life: 1.2 + strength * 0.6, strength, speed: 150 + this.rand() * 50, phase: this.rand() * TAU });
    }

    /** Clear the field outward from (x, y) behind an uneven ring; the caller stops drawing once `cleared`. */
    dissolve(x: number, y: number): void {
        this.ripple(x, y, 1);
        const reach = Math.hypot(Math.max(x, this.w - x), Math.max(y, this.h - y)) * 1.15;
        this.exit = { x, y, t: 0, reach, phase: this.rand() * TAU };
        this.done = false;
    }

    restore(): void {
        this.exit = null;
        this.done = false;
        this.settled = false;
        this.energy.fill(0);
        this.offset.fill(0);
        this.ripples = [];
        this.waves = [];
    }

    energyAt(col: number, row: number): number {
        return this.energy[row * this.cols + col];
    }

    offsetAt(col: number, row: number): [number, number] {
        const i = row * this.cols + col;
        return [this.offset[i * 2], this.offset[i * 2 + 1]];
    }

    center(col: number, row: number): [number, number] {
        return [this.ox + (col + 0.5) * TILE_PITCH, this.oy + (row + 0.5) * TILE_PITCH];
    }

    /** Advance the field; false when nothing on screen changed, so the frame need not be redrawn. */
    step(dt: number): boolean {
        if (this.done) return false;
        // At rest between idle waves nothing moves: skip the per-dot work entirely.
        if (this.settled && this.activity === 'idle' && !this.pointer && !this.ripples.length && !this.waves.length && !this.exit && this.waveIn > dt) {
            this.waveIn -= dt;
            return false;
        }
        this.time += dt;
        const time = this.time;
        const span = this.cols + this.rows * 0.6;
        this.waveIn -= dt;
        if (this.waveIn <= 0) {
            if (this.activity === 'loading') {
                // A model loading: steady left-to-right sweeps.
                this.waveIn = 0.9 + this.rand() * 0.5;
                this.waves.push({ front: -3, speed: 14 + this.rand() * 6, dir: 1, span, strength: 0.5 + this.rand() * 0.2 });
            } else if (this.activity === 'idle') {
                this.waveIn = 3 + this.rand() * 4;
                const dir = this.rand() < 0.5 ? 1 : -1;
                this.waves.push({ front: dir > 0 ? -3 : span + 3, speed: 7 + this.rand() * 4, dir, span, strength: 0.45 });
            } else {
                this.waveIn = 1;
            }
        }
        const p = this.pointer;
        if (p?.down) {
            this.dragIn -= dt;
            if (this.dragIn <= 0) {
                this.dragIn = 0.12;
                this.ripple(p.x, p.y, 0.7);
            }
        }

        const decay = Math.exp(-dt * DECAY_PER_S);
        const attack = Math.min(1, dt * 14);
        const spring = Math.min(1, dt * 9);
        const reach = p ? POINTER_REACH + Math.min(80, p.speed * 0.08) : 0;
        const lift = p ? 0.5 + Math.min(0.45, p.speed / 2000) : 0;
        const level = this.activity === 'download' ? this.progress * this.rows : -1;
        const { cols, rows, energy, offset } = this;
        let peak = 0;
        let drift = 0;
        for (let r = 0; r < rows; r++) {
            for (let c = 0; c < cols; c++) {
                const i = r * cols + c;
                const [x, y] = this.center(c, r);
                const cur = energy[i] * decay;
                let target = 0;
                let px = 0;
                let py = 0;
                // Wavefronts wander and tiles vary, so a sweep reads as work rather than a ruler.
                const grain = 0.55 + 0.45 * Math.sin(c * 1.7 + r * 2.3 + time * 0.6);
                const u = c + r * 0.6 + 0.9 * Math.sin(r * 0.45 + time * 0.8) + 0.5 * Math.sin(c * 0.21 - time * 1.1);
                for (const w of this.waves) {
                    const k = 1 - Math.abs(u - w.front) / 1.5;
                    if (k > 0) target = Math.max(target, k * w.strength * grain);
                }
                for (const q of this.ripples) {
                    const dx = x - q.x, dy = y - q.y, d = Math.hypot(dx, dy) || 1;
                    if (Math.abs(d - q.r) > RIPPLE_BAND + 0.11 * q.r) continue;
                    const a = Math.atan2(dy, dx);
                    const ring = q.r * (1 + 0.07 * Math.sin(5 * a + q.phase) + 0.04 * Math.sin(9 * a - q.phase * 2));
                    const k = 1 - Math.abs(d - ring) / RIPPLE_BAND;
                    if (k <= 0) continue;
                    const fade = (1 - q.t / q.life) ** 1.3;
                    target = Math.max(target, k * 0.85 * q.strength * fade);
                    px += (dx / d) * k * 7 * q.strength * fade;
                    py += (dy / d) * k * 7 * q.strength * fade;
                }
                if (p) {
                    const dx = x - p.x, dy = y - p.y, d = Math.hypot(dx, dy) || 1;
                    if (d < reach) target = Math.max(target, (1 - d / reach) ** 1.4 * lift);
                    if (d < PUSH_REACH) {
                        const push = (1 - d / PUSH_REACH) ** 2 * (10 + Math.min(10, p.speed / 120));
                        px += (dx / d) * push;
                        py += (dy / d) * push;
                    }
                }
                let floor = 0;
                if (level >= 0) {
                    // A download fills the array bottom-up; its surface shimmers.
                    const height = rows - 1 - r + 0.5 + 0.35 * Math.sin(c * 0.55 + time * 2.1) + 0.2 * Math.sin(c * 1.31 - time * 1.3);
                    const below = level - height;
                    if (below > 0) floor = 0.16;
                    if (below > -1 && below < 1) target = Math.max(target, (1 - Math.abs(below)) * (0.45 + 0.35 * Math.sin(time * 3 + this.phase[i])));
                }
                energy[i] = Math.max(floor, target > cur ? cur + (target - cur) * attack : cur);
                offset[i * 2] += (px - offset[i * 2]) * spring;
                offset[i * 2 + 1] += (py - offset[i * 2 + 1]) * spring;
                peak = Math.max(peak, energy[i]);
                drift = Math.max(drift, Math.abs(offset[i * 2]), Math.abs(offset[i * 2 + 1]));
            }
        }
        this.settled = peak < 0.04 && drift < 0.05;
        this.waves = this.waves.filter((w) => {
            w.front += w.dir * w.speed * dt;
            return w.front >= -4 && w.front <= w.span + 4;
        });
        this.ripples = this.ripples.filter((q) => {
            q.t += dt;
            q.r += q.speed * dt * (1 - 0.35 * q.t / q.life);
            return q.t < q.life;
        });
        if (this.exit) {
            this.exit.t += dt;
            if (this.exit.t >= DISSOLVE_S) {
                this.exit = null;
                this.done = true;
            }
        }
        return true;
    }

    draw(ctx: CanvasRenderingContext2D, palette: TilePalette): void {
        ctx.clearRect(0, 0, this.w, this.h);
        if (this.done) return;
        const { cols, rows, energy, offset, mask } = this;
        const rest = palette.dark ? 0.17 : 0.14;
        ctx.lineWidth = 1;
        for (let r = 0; r < rows; r++) {
            for (let c = 0; c < cols; c++) {
                const i = r * cols + c;
                const [cx, cy] = this.center(c, r);
                const x = cx + offset[i * 2], y = cy + offset[i * 2 + 1], v = energy[i], m = mask[i];
                if (v < 0.04) {
                    ctx.fillStyle = palette.fg;
                    ctx.globalAlpha = rest * m;
                    ctx.fillRect(x - 1, y - 1, 2, 2);
                    continue;
                }
                // A dot at work swells into a rounded tile.
                const size = 2 + v * 19, radius = Math.max(1, 6 - v * 3);
                ctx.beginPath();
                ctx.roundRect(x - size / 2, y - size / 2, size, size, radius);
                ctx.fillStyle = palette.accent;
                ctx.globalAlpha = (0.12 + v * 0.22) * m;
                ctx.fill();
                ctx.strokeStyle = palette.accent;
                ctx.globalAlpha = v * 0.5 * m;
                ctx.stroke();
            }
        }
        if (this.exit) this.drawExit(ctx, palette);
        ctx.globalAlpha = 1;
    }

    private drawExit(ctx: CanvasRenderingContext2D, palette: TilePalette): void {
        const x = this.exit as Exit, e = Math.min(1, x.t / DISSOLVE_S);
        const reach = (1 - (1 - e) ** 3) * x.reach;
        const ring = new Path2D();
        for (let k = 0; k <= 180; k++) {
            const a = (k / 180) * TAU;
            const r = reach * (1 + 0.06 * Math.sin(3 * a + x.phase + x.t * 1.5) + 0.03 * Math.sin(5 * a - x.phase * 1.7 - x.t * 2.2));
            const px = x.x + Math.cos(a) * r, py = x.y + Math.sin(a) * r;
            if (k) ring.lineTo(px, py);
            else ring.moveTo(px, py);
        }
        ring.closePath();
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = 'destination-out';
        ctx.fill(ring);
        ctx.globalCompositeOperation = 'source-over';
        ctx.strokeStyle = palette.accent;
        ctx.lineWidth = 1.4;
        ctx.globalAlpha = 0.55 * (1 - e) ** 0.7;
        ctx.stroke(ring);
    }
}
