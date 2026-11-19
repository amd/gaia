// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { log } from '../utils/logger';

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
    /** The last install didn't land: what it tried, and what is still running. */
    installFailed?: { target: string; from: string } | null;
    lastCheckError?: string | null;
    /** Downloaded, and fetching the new backend before the restart is offered. */
    preparing?: boolean;
    /** The older version a bad update can go back to, or null. */
    previousVersion?: string | null;
    /** The app opened but its backend never came up. */
    startFailed?: boolean;
    /** This build can't install updates itself (macOS): offer `downloadUrl` instead. */
    manualInstall?: boolean;
    /** The release page of the offered update, or null. */
    downloadUrl?: string | null;
    /** The renderer couldn't read the status at all; the cause is in the log. */
    readFailed?: boolean;
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
    revertUpdate: () => Promise<UpdateState>;
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

/** What the hook returns: the updater's state, plus a way to read it again. */
export interface UpdateView extends UpdateState {
    /** Ask the updater for its status again, as after a failed read. */
    reload: () => Promise<void>;
}

/**
 * Subscribe to auto-update state from the Electron main process.
 *
 * In non-Electron contexts (browser dev server, tests), `window.gaiaUpdater`
 * is undefined and the hook stays on the initial `idle` state forever,
 * so the indicator never renders. That's the desired behavior: web users
 * don't need a desktop-app update indicator.
 */
export function useUpdateStatus(): UpdateView {
    const [state, setState] = useState<UpdateState>({ ...INITIAL_STATE });
    const mounted = useRef(false);
    const pushes = useRef(0);

    const reload = useCallback(async () => {
        const updater = getUpdaterBridge();
        if (!updater) return;
        const pushesBefore = pushes.current;
        try {
            const s = await updater.getStatus();
            if (mounted.current && s) setState(s);
        } catch (err) {
            log.system.error('Reading the update status failed', err);
            // A status the updater pushed meanwhile is newer than this failure.
            if (mounted.current && pushes.current === pushesBefore) {
                setState((prev) => ({ ...prev, readFailed: true }));
            }
        }
    }, []);

    useEffect(() => {
        const updater = getUpdaterBridge();
        if (!updater) return;

        mounted.current = true;
        const unsubscribe = updater.onStatusChange((s) => {
            if (!s) return;
            pushes.current += 1;
            if (mounted.current) setState(s);
        });
        // Read once now: a status event may have fired before we subscribed.
        void reload();

        return () => {
            mounted.current = false;
            unsubscribe();
        };
    }, [reload]);

    return { ...state, reload };
}
