// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { log } from '../utils/logger';

export const SIDEBAR_MIN_WIDTH = 200;
export const SIDEBAR_MAX_WIDTH = 420;
export const SIDEBAR_DEFAULT_WIDTH = 260;
/** Released narrower than this, the sidebar snaps to its collapsed rail. */
export const SIDEBAR_COLLAPSE_BELOW = 150;
const KEY_STEP = 16;
const WIDTH_KEY = 'gaia-chat-sidebar-width';

export const clampSidebarWidth = (w: number) =>
    Math.round(Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, w)));

export function readSidebarWidth(): number {
    try {
        const saved = Number(localStorage.getItem(WIDTH_KEY));
        return saved ? clampSidebarWidth(saved) : SIDEBAR_DEFAULT_WIDTH;
    } catch (err) {
        log.system.warn('Saved sidebar width unreadable; using the default.', err);
        return SIDEBAR_DEFAULT_WIDTH;
    }
}

function saveSidebarWidth(w: number): void {
    try {
        localStorage.setItem(WIDTH_KEY, String(w));
    } catch (err) {
        log.system.warn('Sidebar width not saved; it resets on reload.', err);
    }
}

/**
 * The sidebar's width and the handle on its right edge: drag to resize, release
 * narrow to collapse, double-click to reset, arrow keys from the keyboard.
 */
export function useSidebarWidth(onCollapse: () => void) {
    const [width, setWidthState] = useState(readSidebarWidth);
    const [dragWidth, setDragWidth] = useState<number | null>(null);

    const setWidth = useCallback((w: number) => {
        const next = clampSidebarWidth(w);
        setWidthState(next);
        saveSidebarWidth(next);
    }, []);

    const startDrag = useCallback((e: PointerEvent<HTMLDivElement>) => {
        if (e.button !== 0) return;
        e.preventDefault();
        const startX = e.clientX;
        const startWidth = width;
        const target = e.currentTarget;
        target.setPointerCapture?.(e.pointerId);
        let raw = startWidth;
        const move = (ev: globalThis.PointerEvent) => {
            raw = startWidth + (ev.clientX - startX);
            setDragWidth(raw < SIDEBAR_COLLAPSE_BELOW ? raw : clampSidebarWidth(raw));
        };
        const up = () => {
            target.removeEventListener('pointermove', move);
            target.removeEventListener('pointerup', up);
            target.removeEventListener('pointercancel', up);
            setDragWidth(null);
            if (raw < SIDEBAR_COLLAPSE_BELOW) onCollapse();
            else setWidth(raw);
        };
        target.addEventListener('pointermove', move);
        target.addEventListener('pointerup', up);
        target.addEventListener('pointercancel', up);
    }, [onCollapse, setWidth, width]);

    const onKey = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
        const next = {
            ArrowLeft: width - KEY_STEP,
            ArrowRight: width + KEY_STEP,
            Home: SIDEBAR_MIN_WIDTH,
            End: SIDEBAR_MAX_WIDTH,
        }[e.key];
        if (next === undefined) return;
        e.preventDefault();
        setWidth(next);
    }, [setWidth, width]);

    const handle = (
        <div
            className={`sb-resize${dragWidth != null ? ' is-dragging' : ''}`}
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize sidebar"
            aria-valuemin={SIDEBAR_MIN_WIDTH}
            aria-valuemax={SIDEBAR_MAX_WIDTH}
            aria-valuenow={Math.round(dragWidth ?? width)}
            tabIndex={0}
            title="Drag to resize, double-click to reset"
            onPointerDown={startDrag}
            onKeyDown={onKey}
            onDoubleClick={() => setWidth(SIDEBAR_DEFAULT_WIDTH)}
        />
    );
    return { width: dragWidth ?? width, resizing: dragWidth != null, handle };
}
