// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useState, type KeyboardEvent, type PointerEvent } from 'react';

const KEY_STEP = 16;

interface DockedResizeOptions {
    width: number;
    /** Stores and clamps the width; called once a drag ends and on every key step. */
    setWidth: (width: number) => void;
    /** Clamps a width while it is being dragged. */
    clamp: (width: number) => number;
    min: number;
    max: number;
    defaultWidth: number;
}

/**
 * The left-edge separator of a pane docked on the right: drag with the
 * pointer, arrow keys step 16px, Home and End jump to the limits, and a
 * double-click restores the default width.
 */
export function useDockedResize({ width, setWidth, clamp, min, max, defaultWidth }: DockedResizeOptions) {
    const [dragWidth, setDragWidth] = useState<number | null>(null);

    const onPointerDown = useCallback((e: PointerEvent<HTMLDivElement>) => {
        if (e.button !== 0) return;
        e.preventDefault();
        const startX = e.clientX;
        const startWidth = width;
        const target = e.currentTarget;
        target.setPointerCapture?.(e.pointerId);
        let latest = startWidth;
        const move = (ev: globalThis.PointerEvent) => {
            latest = clamp(startWidth + (startX - ev.clientX));
            setDragWidth(latest);
        };
        const up = () => {
            target.removeEventListener('pointermove', move);
            target.removeEventListener('pointerup', up);
            target.removeEventListener('pointercancel', up);
            setDragWidth(null);
            setWidth(latest);
        };
        target.addEventListener('pointermove', move);
        target.addEventListener('pointerup', up);
        target.addEventListener('pointercancel', up);
    }, [clamp, setWidth, width]);

    const onKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
        const next = {
            ArrowLeft: width + KEY_STEP,
            ArrowRight: width - KEY_STEP,
            Home: min,
            End: max,
        }[e.key];
        if (next === undefined) return;
        e.preventDefault();
        setWidth(next);
    }, [max, min, setWidth, width]);

    const shown = dragWidth ?? width;
    return {
        width: shown,
        dragging: dragWidth !== null,
        separatorProps: {
            role: 'separator',
            'aria-orientation': 'vertical',
            'aria-valuemin': min,
            'aria-valuemax': max,
            'aria-valuenow': shown,
            tabIndex: 0,
            onPointerDown,
            onKeyDown,
            onDoubleClick: () => setWidth(defaultWidth),
        } as const,
    };
}
