// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The voice shape: one canvas, one look per state, so you can tell who has the
 * floor without reading. Listening and speaking follow the live audio level.
 */

import { useEffect, useRef } from 'react';
import { prefersReducedMotion } from '../../utils/motion';

export type VizState = 'warming' | 'listening' | 'thinking' | 'speaking' | 'paused';

type Colors = Record<'blue' | 'gold' | 'goldLight' | 'border' | 'muted', string>;

const TAU = Math.PI * 2;

/** `#rrggbb` mixed toward white by `amount` (0-1); other forms come back as they are. */
export function lighten(color: string, amount: number): string {
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(color);
    if (!m) return color;
    const ch = (hex: string) => Math.round(parseInt(hex, 16) + (255 - parseInt(hex, 16)) * amount);
    return `rgb(${ch(m[1])}, ${ch(m[2])}, ${ch(m[3])})`;
}

function readColors(el: Element): Colors {
    const cs = getComputedStyle(el);
    const v = (name: string) => cs.getPropertyValue(name).trim();
    const gold = v('--accent-solid');
    return { blue: v('--accent-blue'), gold, goldLight: lighten(gold, 0.55), border: v('--border'), muted: v('--text-muted') };
}

class Shape {
    state: VizState = 'paused';
    prev: VizState | null = null;
    mix = 1;
    level = 0;
    bars = new Float32Array(72);
    ripples: Array<{ r: number; al: number }> = [];
    lastRip = 0;
    seed = Math.random() * 10;
    col: Colors = { blue: '', gold: '', goldLight: '', border: '', muted: '' };

    constructor(readonly reduced: boolean) {}

    set(s: VizState) {
        if (s === this.state) return;
        this.prev = this.state;
        this.state = s;
        this.mix = this.reduced ? 1 : 0;
    }

    frame(g: CanvasRenderingContext2D, w: number, now: number, target: number) {
        const t = this.reduced ? 0 : now / 1000;
        const tg = this.reduced ? 0 : target;
        this.level += (tg - this.level) * (tg > this.level ? 0.45 : 0.12);
        // About 250 ms at 60 fps.
        if (this.mix < 1) this.mix = Math.min(1, this.mix + 0.065);
        g.clearRect(0, 0, w, w);
        const R = w * 0.23;
        if (this.mix < 1 && this.prev) this.draw(this.prev, g, w / 2, R, t, 1 - this.mix, false, w);
        this.draw(this.state, g, w / 2, R, t, this.mix, true, w);
        g.globalAlpha = 1;
    }

    glow(g: CanvasRenderingContext2D, cx: number, r: number, color: string, alpha: number) {
        if (r <= 0 || !color) return;
        const gr = g.createRadialGradient(cx, cx, r * 0.15, cx, cx, r);
        gr.addColorStop(0, color);
        gr.addColorStop(1, 'transparent');
        g.globalAlpha = alpha;
        g.fillStyle = gr;
        g.beginPath();
        g.arc(cx, cx, r, 0, TAU);
        g.fill();
    }

    draw(s: VizState, g: CanvasRenderingContext2D, cx: number, R: number, t: number, a: number, cur: boolean, w: number) {
        const k = cur ? 0.5 + 0.5 * this.mix : 0.25 + 0.75 * a;
        const L = this.level;
        const circle = (r: number) => { g.beginPath(); g.arc(cx, cx, Math.max(0, r), 0, TAU); };
        if (s === 'listening') {
            const N = this.bars.length;
            const rr = R * (0.6 + 0.4 * k);
            const blue = this.col.blue;
            this.glow(g, cx, R * (1.5 + 0.6 * L), blue, a * (0.08 + 0.16 * L));
            g.strokeStyle = g.fillStyle = blue;
            g.lineCap = 'round';
            g.lineWidth = Math.max(2, R * 0.058);
            for (let i = 0; i < N; i++) {
                const an = (i / N) * TAU - Math.PI / 2;
                const n = 0.5 + 0.5 * Math.sin(an * 3 + t * 4.7 + this.seed) * Math.cos(an * 5 - t * 3.1);
                const idle = 0.045 + 0.03 * Math.sin(an * 2 + t * 1.6);
                if (cur) this.bars[i] += (Math.max(idle, L * (0.18 + 0.82 * n)) - this.bars[i]) * 0.32;
                const b = this.bars[i];
                const r0 = rr + 4;
                const len = R * 0.95 * b * k + 0.5;
                g.globalAlpha = a * (0.32 + 0.68 * Math.min(1, b * 1.8));
                g.beginPath();
                g.moveTo(cx + Math.cos(an) * r0, cx + Math.sin(an) * r0);
                g.lineTo(cx + Math.cos(an) * (r0 + len), cx + Math.sin(an) * (r0 + len));
                g.stroke();
            }
            g.globalAlpha = a * 0.1;
            circle(rr);
            g.fill();
            g.globalAlpha = a;
            circle(R * 0.2 * (1 + L * 0.8) * k);
            g.fill();
        } else if (s === 'speaking') {
            const gold = this.col.gold;
            const grow = cur ? 0.6 + 0.4 * this.mix : 1;
            const base = R * 0.82 * grow * (1 + 0.28 * L);
            this.glow(g, cx, base * (1.9 + 0.5 * L), gold, a * (0.12 + 0.22 * L));
            if (cur && !this.reduced && L > 0.55 && t - this.lastRip > 0.22) {
                this.ripples.push({ r: base, al: 0.42 });
                this.lastRip = t;
            }
            if (cur) this.ripples = this.ripples.filter((p) => { p.r += w * 0.0042; p.al *= 0.952; return p.al > 0.02 && p.r < w * 0.5; });
            g.strokeStyle = gold;
            g.lineWidth = 1.5;
            for (const p of this.ripples) { g.globalAlpha = a * p.al; circle(p.r); g.stroke(); }
            const amp = 0.018 + 0.075 * L;
            g.beginPath();
            for (let j = 0; j <= 120; j++) {
                const th = (j / 120) * TAU;
                const r = base * (1 + amp * (Math.sin(3 * th + t * 2.1) + 0.6 * Math.sin(5 * th - t * 3.3 + 1) + 0.4 * Math.sin(7 * th + t * 4.6)));
                const x = cx + Math.cos(th) * r;
                const y = cx + Math.sin(th) * r;
                if (j) g.lineTo(x, y);
                else g.moveTo(x, y);
            }
            const gr = g.createRadialGradient(cx - base * 0.35, cx - base * 0.4, base * 0.05, cx, cx, base * 1.15);
            gr.addColorStop(0, this.col.goldLight || gold);
            gr.addColorStop(0.55, gold);
            gr.addColorStop(1, gold);
            g.fillStyle = gr;
            g.globalAlpha = a;
            g.fill();
        } else if (s === 'thinking') {
            const gold = this.col.gold;
            g.fillStyle = g.strokeStyle = gold;
            g.lineCap = 'round';
            this.glow(g, cx, R * 1.4, gold, a * 0.1);
            g.globalAlpha = a * 0.9;
            circle(R * 0.42 * (1 + 0.05 * Math.sin(t * 2.4)) * (cur ? 0.75 + 0.25 * this.mix : 1));
            g.fill();
            g.lineWidth = 2.5;
            g.globalAlpha = a * 0.85;
            g.beginPath();
            g.arc(cx, cx, R * 0.82, t * 3, t * 3 + 1.2);
            g.stroke();
            g.lineWidth = 2;
            g.globalAlpha = a * 0.35;
            g.beginPath();
            g.arc(cx, cx, R * 1.05, -t * 1.8 + 2, -t * 1.8 + 2.7);
            g.stroke();
        } else if (s === 'warming') {
            g.fillStyle = g.strokeStyle = this.col.muted;
            g.lineCap = 'round';
            g.lineWidth = 2;
            g.globalAlpha = a * 0.25;
            circle(R * 0.42);
            g.fill();
            g.globalAlpha = a * 0.6;
            g.beginPath();
            g.arc(cx, cx, R * 0.82, t * 2.2, t * 2.2 + 0.8 + 0.4 * Math.sin(t * 1.5));
            g.stroke();
        } else {
            g.strokeStyle = g.fillStyle = this.col.border;
            g.lineWidth = 1.5;
            g.globalAlpha = a * 0.8;
            circle(R * 0.82);
            g.stroke();
            g.globalAlpha = a * 0.35;
            circle(R * 0.42);
            g.fill();
        }
    }
}

interface VoiceVizProps {
    state: VizState;
    /** The live level, 0–1: the mic while listening, playback while speaking. */
    level: () => number;
    className?: string;
}

export function VoiceViz({ state, level, className }: VoiceVizProps) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const shapeRef = useRef<Shape | null>(null);
    const levelRef = useRef(level);
    levelRef.current = level;

    if (!shapeRef.current) shapeRef.current = new Shape(prefersReducedMotion());
    shapeRef.current.set(state);

    useEffect(() => {
        const canvas = canvasRef.current;
        const shape = shapeRef.current;
        if (!canvas || !shape) return;
        const g = canvas.getContext('2d');
        if (!g) return;
        let frame = 0;
        let raf = 0;
        const loop = (now: number) => {
            raf = requestAnimationFrame(loop);
            // Nothing to draw while hidden.
            if (document.hidden || !canvas.isConnected) return;
            const w = canvas.clientWidth;
            if (!w) return;
            const dpr = Math.min(2, window.devicePixelRatio || 1);
            const px = Math.round(w * dpr);
            if (canvas.width !== px) canvas.width = canvas.height = px;
            if (frame++ % 30 === 0) shape.col = readColors(canvas);
            g.setTransform(dpr, 0, 0, dpr, 0, 0);
            const live = shape.state === 'listening' || shape.state === 'speaking';
            shape.frame(g, w, now, live ? levelRef.current() : 0);
            // Reduced motion: the still shape is drawn once the crossfade settles.
            if (shape.mix >= 1 && shape.reduced) cancelAnimationFrame(raf);
        };
        raf = requestAnimationFrame(loop);
        return () => cancelAnimationFrame(raf);
    }, [state]);

    return <canvas ref={canvasRef} className={className} aria-hidden="true" data-state={state} />;
}
