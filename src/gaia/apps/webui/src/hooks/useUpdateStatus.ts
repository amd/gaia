// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';

/**
 * Auto-update status surfaced by the Electron main process via the
 * `gaiaUpdater` contextBridge API (see src/gaia/apps/webui/preload.cjs
 * and services/auto-updater.cjs). Phase F of the desktop installer plan.
 */
export type UpdateStatus =
    | 'idle'
    | 'checking'
    | 'available'
    | 'downloading'
    | 'downloaded'
    | 'error'
    | 'disabled';

export interface UpdateState {
    status: UpdateStatus;
    version: string | null;
    progress: number;
    releaseNotes: string | null;
    error: string | null;
}

interface GaiaUpdaterBridge {
    getStatus: () => Promise<UpdateState>;
    check: () => Promise<UpdateState>;
    onStatusChange: (cb: (state: UpdateState) => void) => () => void;
}

const INITIAL_STATE: UpdateState = {
    status: 'idle',
    version: null,
    progress: 0,
    releaseNotes: null,
    error: null,
};

/**
 * Subscribe to auto-update state from the Electron main process.
 *
 * In non-Electron contexts (browser dev server, tests), `window.gaiaUpdater`
 * is undefined and the hook stays on the initial `idle` state forever,
 * so the indicator never renders. That's the desired behavior: web users
 * don't need a desktop-app update indicator.
 */
export function useUpdateStatus(): UpdateState {
    const [state, setState] = useState<UpdateState>(INITIAL_STATE);

    useEffect(() => {
        const updater: GaiaUpdaterBridge | undefined = (window as unknown as {
            gaiaUpdater?: GaiaUpdaterBridge;
        }).gaiaUpdater;
        if (!updater) return;

        let mounted = true;

        // Fetch the current state immediately (covers the race where a
        // status event fires before we mount).
        updater
            .getStatus()
            .then((s) => {
                if (mounted && s) setState(s);
            })
            .catch(() => {
                // Swallow — the subscription below will still work and
                // surface any actual update events.
            });

        const unsubscribe = updater.onStatusChange((s) => {
            if (mounted && s) setState(s);
        });

        return () => {
            mounted = false;
            try {
                unsubscribe();
            } catch {
                // ignore
            }
        };
    }, []);

    return state;
}
