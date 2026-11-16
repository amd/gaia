// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';

import { MEMORY_ERROR_NOTICE_MS, MEMORY_NOTICE_MS } from '@/api/constants';

export interface MemoryNotice {
    tone: 'success' | 'danger' | 'info';
    text: string;
    /** Stays until replaced or dismissed, e.g. while an operation runs. */
    sticky?: boolean;
    /** A button on the notice, e.g. Undo. */
    action?: { label: string; onPress: () => void };
}

/** The screen's one notice slot: a new notice replaces the last, and clears itself like the web's toasts. */
export function useMemoryNotice() {
    const [notice, setNotice] = useState<MemoryNotice | null>(null);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

    const clearTimer = () => {
        if (timer.current) clearTimeout(timer.current);
        timer.current = null;
    };
    const dismiss = useCallback(() => {
        clearTimer();
        setNotice(null);
    }, []);
    const notify = useCallback((next: MemoryNotice) => {
        clearTimer();
        setNotice(next);
        if (!next.sticky) timer.current = setTimeout(() => setNotice(null), next.tone === 'danger' ? MEMORY_ERROR_NOTICE_MS : MEMORY_NOTICE_MS);
    }, []);
    useEffect(() => clearTimer, []);

    return { notice, notify, dismiss };
}
