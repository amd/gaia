// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';
import type { BackendLifecycle } from '../types/agent';
import { setApiBase } from '../utils/apiBase';
import { log } from '../utils/logger';

interface BackendState {
    /** The desktop app's view of its backend; null outside it, or until it answers. */
    lifecycle: BackendLifecycle | null;
    /** Why the last Restart GAIA failed, or null. */
    restartError: string | null;
    apply: (state: BackendLifecycle) => void;
    restart: () => Promise<void>;
}

export const useBackendStore = create<BackendState>((set, get) => ({
    lifecycle: null,
    restartError: null,
    apply: (state) => {
        // A restarted backend listens on a new port: every later request goes there.
        if (state.apiBase) setApiBase(state.apiBase);
        set({ lifecycle: state, restartError: state.status === 'running' ? null : get().restartError });
    },
    restart: async () => {
        const bridge = window.gaiaAPI?.backend;
        if (!bridge) throw new Error('Restart GAIA needs the desktop app.');
        set({ restartError: null });
        try {
            get().apply(await bridge.restart());
        } catch (err) {
            log.system.error('Restart GAIA failed', err);
            set({ restartError: err instanceof Error ? err.message : String(err) });
        }
    },
}));

/**
 * Ask the desktop app where its backend is now. A window that missed the
 * restart push, or reloaded back to its startup port, otherwise never finds it.
 */
export function resyncBackend(): void {
    const bridge = window.gaiaAPI?.backend;
    if (!bridge) return;
    bridge.getState()
        .then((state) => useBackendStore.getState().apply(state))
        .catch((err) => log.system.error('Could not read the backend state from the desktop app', err));
}

/**
 * Follow the desktop app's backend restarts. `onBack` runs when a restarted
 * backend is up, so the caller can re-check status at once. Returns the unsubscribe.
 */
export function followBackendLifecycle(onBack: () => void): () => void {
    const bridge = window.gaiaAPI?.backend;
    if (!bridge) return () => {};
    const apply = (state: BackendLifecycle) => {
        const was = useBackendStore.getState().lifecycle?.status;
        useBackendStore.getState().apply(state);
        if (state.status === 'running' && was && was !== 'running') onBack();
    };
    const off = bridge.onState(apply);
    bridge.getState()
        .then(apply)
        .catch((err) => log.system.error('Could not read the backend state from the desktop app', err));
    return off;
}
