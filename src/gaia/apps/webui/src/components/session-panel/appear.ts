// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The session panel grows as the chat does: a section or row that turns up
 * after the panel has settled eases in, so the eye is led to what is new.
 * What is there when the panel opens, or when the chat is switched, is simply
 * there.
 */

import { createContext, useContext, useEffect, useRef, useState } from 'react';

/** How long after opening (or switching chat) first reads may still land without calling attention. */
export const SETTLE_MS = 900;

/** True once the panel's first reads have landed. */
export const PanelSettled = createContext(false);

/** Whether the panel showing *sessionId* has settled; false again each time the chat changes. */
export function useSettled(sessionId: string): boolean {
    const [settledFor, setSettledFor] = useState<string | null>(null);
    useEffect(() => {
        const timer = window.setTimeout(() => setSettledFor(sessionId), SETTLE_MS);
        return () => window.clearTimeout(timer);
    }, [sessionId]);
    return settledFor === sessionId;
}

/** The class that eases an element in, for one that first rendered after the panel settled. */
export function useAppear(): string {
    const settled = useContext(PanelSettled);
    const late = useRef(settled);
    return late.current ? ' sp-appear' : '';
}
