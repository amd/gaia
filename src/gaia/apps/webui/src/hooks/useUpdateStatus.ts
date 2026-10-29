// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';

/**
 * Auto-update status surfaced by the Electron main process via the
 * `gaiaUpdater` contextBridge API (see src/gaia/apps/webui/preload.cjs
 * and services/auto-updater.cjs). Phase F of the desktop installer plan.
 * Issue #1336: adds rollback support (listReleases, installVersion, resumeUpdates).
 * Auto-update is opt-in (off by default): a found update is offered, and
 * downloadUpdate() installs it.
 */
export type UpdateStatus =
    | 'idle'
    | 'checking'
    | 'available'
    | 'downloading'
    | 'downloaded'
    | 'error'
    | 'disabled'
    | 'no-channel';

export interface UpdateState {
    status: UpdateStatus;
    version: string | null;
    progress: number;
    releaseNotes: string | null;
    error: string | null;
    /** The version currently running in the app (from app.getVersion()). */
    currentVersion: string | null;
    /** The pinned version tag (e.g. "v0.20.0") or null if not pinned. */
    pinnedVersion: string | null;
    /** Download and install updates without asking. Off by default. */
    autoUpdate: boolean;
    /** Offer release candidates too. Off by default; independent of autoUpdate. */
    includePrereleases?: boolean;
    /** ~/.gaia/update-config.json could not be read; why, or null. */
    configError?: string | null;
}

/** Info about a single published release, as returned by listReleases(). */
export interface ReleaseInfo {
    version: string;
    tag: string;
    date: string | null;
    notesUrl: string | null;
    isCurrent: boolean;
    isPinned: boolean;
}

export interface GaiaUpdaterBridge {
    getStatus: () => Promise<UpdateState>;
    check: () => Promise<UpdateState>;
    onStatusChange: (cb: (state: UpdateState) => void) => () => void;
    listReleases: () => Promise<ReleaseInfo[] | { error: string }>;
    installVersion: (tag: string) => Promise<UpdateState>;
    resumeUpdates: () => Promise<UpdateState>;
    setAutoUpdate: (enabled: boolean) => Promise<UpdateState>;
    setIncludePrereleases: (enabled: boolean) => Promise<UpdateState>;
    downloadUpdate: () => Promise<UpdateState>;
    restart: () => Promise<UpdateState>;
}

/** The Electron updater bridge, or undefined outside the desktop app. */
export function getUpdaterBridge(): GaiaUpdaterBridge | undefined {
    return (window as unknown as { gaiaUpdater?: GaiaUpdaterBridge }).gaiaUpdater;
}

export const INITIAL_STATE: UpdateState = {
    status: 'idle',
    version: null,
    progress: 0,
    releaseNotes: null,
    error: null,
    currentVersion: null,
    pinnedVersion: null,
    autoUpdate: false,
    includePrereleases: false,
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
    const [state, setState] = useState<UpdateState>({ ...INITIAL_STATE });

    useEffect(() => {
        const updater = getUpdaterBridge();
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
