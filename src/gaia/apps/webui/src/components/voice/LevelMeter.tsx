// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { prefersReducedMotion } from '../../utils/motion';

/** A short scrolling waveform of the mic level, for dictation. */
export function LevelMeter({ level, className }: { level: () => number; className?: string }) {
    const ref = useRef<HTMLCanvasElement>(null);
    const levelRef = useRef(level);
    levelRef.current = level;

    useEffect(() => {
        const c = ref.current;
        const g = c?.getContext('2d');
        if (!c || !g) return;
        const reduced = prefersReducedMotion();
        const hist = new Array<number>(16).fill(0);
        let last = 0;
        let raf = 0;
        let color = '';
        const loop = (now: number) => {
            raf = requestAnimationFrame(loop);
            if (document.hidden) return;
            const w = c.clientWidth;
            const h = c.clientHeight;
            if (!w || !h) return;
            const dpr = Math.min(2, window.devicePixelRatio || 1);
            if (c.width !== Math.round(w * dpr)) {
                c.width = Math.round(w * dpr);
                c.height = Math.round(h * dpr);
            }
            if (!color) color = getComputedStyle(c).getPropertyValue('--danger').trim();
            if (now - last > 70) {
                hist.push(reduced ? 0.3 : levelRef.current());
                hist.shift();
                last = now;
            }
            g.setTransform(dpr, 0, 0, dpr, 0, 0);
            g.clearRect(0, 0, w, h);
            g.fillStyle = color;
            const bw = 2.5;
            const gap = (w - hist.length * bw) / (hist.length - 1);
            hist.forEach((v, i) => {
                const bh = Math.max(2.5, v * h);
                g.globalAlpha = 0.45 + 0.55 * v;
                g.fillRect(i * (bw + gap), (h - bh) / 2, bw, bh);
            });
            g.globalAlpha = 1;
        };
        raf = requestAnimationFrame(loop);
        return () => cancelAnimationFrame(raf);
    }, []);

    return <canvas ref={ref} className={className} aria-hidden="true" />;
}
