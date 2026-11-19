// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import type { PermissionMode } from '../types';

/** Keeps the composer chip and the session panel showing the same mode. */
const EVENT = 'gaia:permission-mode';

interface Detail {
    sessionId: string;
    mode: PermissionMode;
}

export function announcePermissionMode(sessionId: string, mode: PermissionMode): void {
    window.dispatchEvent(new CustomEvent<Detail>(EVENT, { detail: { sessionId, mode } }));
}

export function usePermissionModeEvents(sessionId: string | null, onMode: (mode: PermissionMode) => void): void {
    const cb = useRef(onMode);
    cb.current = onMode;
    useEffect(() => {
        if (!sessionId) return;
        const handler = (e: Event) => {
            const detail = (e as CustomEvent<Detail>).detail;
            if (detail?.sessionId === sessionId) cb.current(detail.mode);
        };
        window.addEventListener(EVENT, handler);
        return () => window.removeEventListener(EVENT, handler);
    }, [sessionId]);
}
