// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { useMediaQuery } from '../../hooks/useMediaQuery';
import { REDUCED_MOTION_QUERY } from '../../utils/motion';

export function useReducedMotion(): boolean {
    return useMediaQuery(REDUCED_MOTION_QUERY);
}

/** `value`, eased to over `ms` with an ease-out; instant under reduced motion. */
export function useTweened(value: number, ms = 150): number {
    const reduced = useReducedMotion();
    const [shown, setShown] = useState(value);
    const from = useRef(value);
    useEffect(() => {
        if (reduced || typeof requestAnimationFrame !== 'function') {
            from.current = value;
            setShown(value);
            return;
        }
        const start = from.current;
        const began = performance.now();
        let frame = 0;
        const step = (now: number) => {
            const k = Math.min(1, (now - began) / ms);
            const eased = 1 - (1 - k) ** 3;
            const next = start + (value - start) * eased;
            from.current = next;
            setShown(next);
            if (k < 1) frame = requestAnimationFrame(step);
        };
        frame = requestAnimationFrame(step);
        return () => cancelAnimationFrame(frame);
    }, [value, ms, reduced]);
    return shown;
}

/** `value` once it has held still for `ms`: for a live region that must not chatter. */
export function useDebounced<T>(value: T, ms: number): T {
    const [settled, setSettled] = useState(value);
    useEffect(() => {
        const t = setTimeout(() => setSettled(value), ms);
        return () => clearTimeout(t);
    }, [value, ms]);
    return settled;
}

/** Whether the page is visible; loops pause while it is hidden. */
export function usePageVisible(): boolean {
    const [visible, setVisible] = useState(() => typeof document === 'undefined' || document.visibilityState !== 'hidden');
    useEffect(() => {
        const sync = () => setVisible(document.visibilityState !== 'hidden');
        document.addEventListener('visibilitychange', sync);
        return () => document.removeEventListener('visibilitychange', sync);
    }, []);
    return visible;
}
