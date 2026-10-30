// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';

/** Files were put back by an Undo, so lists of what a chat changed are stale. */
const EVENT = 'gaia:changes-undone';

export function announceUndo(sessionId: string): void {
    window.dispatchEvent(new CustomEvent<string>(EVENT, { detail: sessionId }));
}

export function useUndoEvents(sessionId: string, onUndo: () => void): void {
    const cb = useRef(onUndo);
    cb.current = onUndo;
    useEffect(() => {
        const handler = (e: Event) => {
            if ((e as CustomEvent<string>).detail === sessionId) cb.current();
        };
        window.addEventListener(EVENT, handler);
        return () => window.removeEventListener(EVENT, handler);
    }, [sessionId]);
}
