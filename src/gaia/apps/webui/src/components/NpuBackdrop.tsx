// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, type RefObject } from 'react';
import { TileField, type TileActivity, type TileHole, type TilePalette } from './npuTiles';
import { log } from '../utils/logger';
import { REDUCED_MOTION_QUERY } from '../utils/motion';
import './NpuBackdrop.css';

const FRAME_MS = 1000 / 30;
const KEYSTROKE_GAP_MS = 90;

interface NpuBackdropProps {
    /** What GAIA is doing, which sets how the dots move. */
    activity: TileActivity;
    /** Download progress, 0 to 1, while `activity` is `download`. */
    progress?: number;
    /** Changes on each keystroke; each change sends a small ripple out from the composer. */
    pulse: number;
    /** The composer sits at the bottom, so only it is dimmed behind, not the greeting's old place. */
    docked: boolean;
    /** The greeting and composer, which the dots dim behind. */
    contentRef: RefObject<HTMLElement | null>;
    /** The composer: where ripples and the send dissolve start. */
    composerRef: RefObject<HTMLElement | null>;
}

function readPalette(): TilePalette {
    const css = getComputedStyle(document.documentElement);
    return {
        fg: css.getPropertyValue('--text-primary').trim(),
        accent: css.getPropertyValue('--accent-solid').trim(),
        dark: document.documentElement.getAttribute('data-theme') === 'dark',
    };
}

function holeOf(el: HTMLElement, surface: DOMRect, pad: number): TileHole | null {
    const c = el.getBoundingClientRect();
    if (!c.width) return null;
    return { cx: c.left - surface.left + c.width / 2, cy: c.top - surface.top + c.height / 2, rx: c.width / 2 + pad, ry: c.height / 2 + pad + 10 };
}

/** The new-chat background: NPU-array dots behind the content, mounted as the parent's first child. */
export function NpuBackdrop({ activity, progress = 0, pulse, docked, contentRef, composerRef }: NpuBackdropProps) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const fieldRef = useRef<TileField | null>(null);
    const surfaceRef = useRef<HTMLElement | null>(null);
    const startRef = useRef<() => void>(() => {});
    const refreshRef = useRef<() => void>(() => {});
    const dockedRef = useRef(docked);
    const lastPulse = useRef({ value: pulse, at: 0 });
    dockedRef.current = docked;

    useEffect(() => {
        const canvas = canvasRef.current;
        const surface = canvas?.parentElement;
        if (!canvas || !surface) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
            log.ui.warn('New-chat background is off: the browser gave no 2D canvas context (often low graphics memory).');
            return;
        }
        surfaceRef.current = surface;
        const field = new TileField();
        fieldRef.current = field;
        const reduced = typeof window.matchMedia === 'function' ? window.matchMedia(REDUCED_MOTION_QUERY) : null;
        let palette = readPalette();
        let raf = 0;
        let last = 0;
        let running = false;
        let holeKey = '';
        const pointer = { x: 0, y: 0, at: 0, speed: 0 };

        const draw = () => field.draw(ctx, palette);
        // Track the content box; it moves when the composer docks at the bottom.
        const syncHole = (box: DOMRect, force = false) => {
            const el = dockedRef.current ? composerRef.current : contentRef.current;
            const hole = (el && holeOf(el, box, dockedRef.current ? 24 : 40)) ?? { cx: box.width / 2, cy: box.height / 2, rx: 1, ry: 1 };
            const key = `${Math.round(hole.cx)},${Math.round(hole.cy)},${Math.round(hole.rx)},${Math.round(hole.ry)}`;
            if (force || key !== holeKey) {
                holeKey = key;
                return hole;
            }
            return null;
        };
        const size = () => {
            const box = surface.getBoundingClientRect();
            if (!box.width || !box.height) return;
            const dpr = Math.min(2, window.devicePixelRatio || 1);
            canvas.width = Math.round(box.width * dpr);
            canvas.height = Math.round(box.height * dpr);
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            field.resize(box.width, box.height, syncHole(box, true) as TileHole);
            draw();
        };
        const tick = (now: number) => {
            raf = requestAnimationFrame(tick);
            if (now - last < FRAME_MS - 1.5) return;
            const dt = Math.min(0.1, (now - last) / 1000);
            last = now;
            const moved = syncHole(surface.getBoundingClientRect());
            if (moved) field.setHole(moved);
            if (now - pointer.at > 80) pointer.speed *= 0.8;
            if (field.step(dt) || moved) draw();
            // A send has dissolved the field: nothing left to draw until it is restored.
            if (field.cleared) stop();
        };
        const start = () => {
            if (running || document.hidden || reduced?.matches || field.cleared) return;
            running = true;
            last = performance.now();
            raf = requestAnimationFrame(tick);
        };
        const stop = () => {
            running = false;
            cancelAnimationFrame(raf);
        };
        startRef.current = start;
        refreshRef.current = () => {
            const moved = syncHole(surface.getBoundingClientRect());
            if (moved) field.setHole(moved);
            if (!running) draw();
        };
        const onVisibility = () => (document.hidden ? stop() : start());
        const onMotion = () => {
            stop();
            draw();
            start();
        };
        const onTheme = () => {
            palette = readPalette();
            draw();
        };
        const onMove = (e: PointerEvent) => {
            const box = surface.getBoundingClientRect();
            const x = e.clientX - box.left, y = e.clientY - box.top, now = performance.now();
            if (pointer.at && now > pointer.at) {
                const v = Math.hypot(x - pointer.x, y - pointer.y) / ((now - pointer.at) / 1000);
                pointer.speed += (Math.min(4000, v) - pointer.speed) * 0.35;
            }
            Object.assign(pointer, { x, y, at: now });
            const onContent = e.target instanceof Element && e.target.closest('button, a, input, textarea, .composer');
            field.setPointer(x, y, (e.buttons & 1) === 1 && !onContent, pointer.speed);
        };
        const onLeave = () => {
            pointer.at = 0;
            pointer.speed = 0;
            field.clearPointer();
        };

        const resizer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(size);
        // Border box: docking only moves padding, which must not reset the field.
        resizer?.observe(surface, { box: 'border-box' });
        const themeWatch = new MutationObserver(onTheme);
        // Theme, palette and custom-palette inline styles all change the tokens.
        themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-palette', 'style'] });
        document.addEventListener('visibilitychange', onVisibility);
        reduced?.addEventListener('change', onMotion);
        surface.addEventListener('pointermove', onMove);
        surface.addEventListener('pointerleave', onLeave);
        size();
        start();

        return () => {
            stop();
            resizer?.disconnect();
            themeWatch.disconnect();
            document.removeEventListener('visibilitychange', onVisibility);
            reduced?.removeEventListener('change', onMotion);
            surface.removeEventListener('pointermove', onMove);
            surface.removeEventListener('pointerleave', onLeave);
            fieldRef.current = null;
            startRef.current = () => {};
            refreshRef.current = () => {};
        };
    }, [contentRef, composerRef]);

    const composerCenter = useCallback((): [number, number] | null => {
        const surface = surfaceRef.current, composer = composerRef.current;
        if (!surface || !composer) return null;
        const s = surface.getBoundingClientRect(), o = composer.getBoundingClientRect();
        return [o.left - s.left + o.width / 2, o.top - s.top + o.height / 2];
    }, [composerRef]);

    useEffect(() => {
        const field = fieldRef.current;
        if (!field) return;
        field.setActivity(activity);
        if (activity === 'sending') {
            const at = composerCenter();
            if (at) field.dissolve(...at);
        } else if (field.dissolving) {
            // The send failed and this screen stayed, possibly mid-dissolve: bring the field back.
            field.restore();
            startRef.current();
        }
    }, [activity, composerCenter]);

    useEffect(() => {
        fieldRef.current?.setProgress(progress);
    }, [progress]);

    // With motion off nothing redraws on its own, so follow the composer when it docks.
    useEffect(() => {
        refreshRef.current();
    }, [docked]);

    useEffect(() => {
        const seen = lastPulse.current;
        if (pulse === seen.value) return;
        const now = performance.now();
        seen.value = pulse;
        if (now - seen.at < KEYSTROKE_GAP_MS) return;
        seen.at = now;
        const at = composerCenter();
        if (at) fieldRef.current?.ripple(...at, 0.45);
    }, [pulse, composerCenter]);

    return <canvas ref={canvasRef} className="npu-backdrop" aria-hidden="true" />;
}
