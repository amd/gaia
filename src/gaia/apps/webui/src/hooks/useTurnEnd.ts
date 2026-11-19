// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';

/** Call *onEnd* each time *busy* goes from true to false: a reply finished. */
export function useTurnEnd(busy: boolean, onEnd: () => void): void {
    const was = useRef(busy);
    const cb = useRef(onEnd);
    cb.current = onEnd;
    useEffect(() => {
        const ended = was.current && !busy;
        was.current = busy;
        if (ended) cb.current();
    }, [busy]);
}

const STARTING_REREAD_MS = 2000;

/**
 * Call *reread* every 2 s while a reply runs and the agent read as not started.
 * A read made before the agent came up would otherwise stand until the reply
 * ends, which a reply waiting on a permission prompt doesn't do.
 */
export function useRereadWhileStarting(busy: boolean, notStarted: boolean, reread: () => void): void {
    const cb = useRef(reread);
    cb.current = reread;
    const [tick, setTick] = useState(0);
    useEffect(() => {
        if (!busy || !notStarted) return;
        const timer = window.setTimeout(() => { cb.current(); setTick((n) => n + 1); }, STARTING_REREAD_MS);
        return () => window.clearTimeout(timer);
    }, [busy, notStarted, tick]);
}
