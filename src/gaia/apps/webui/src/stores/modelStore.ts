// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The model new turns run on, and where it runs (this PC or a cloud provider). */

import { create } from 'zustand';
import * as api from '../services/api';
import type { ActiveModel } from '../types';

interface ModelState {
    active: ActiveModel | null;
    error: string | null;
    /** One-line notice about restoring the last model, shown once per launch. */
    restoreNotice: { ok: boolean; message: string } | null;
    refresh: () => Promise<void>;
    select: (model: string) => Promise<void>;
    dismissRestoreNotice: () => void;
}

const RESTORE_RETRY_MS = 5_000;
const RESTORE_RETRIES = 24;
let restoreRetries = 0;

export const useModelStore = create<ModelState>((set, get) => ({
    active: null,
    error: null,
    restoreNotice: null,
    refresh: async () => {
        try {
            const active = await api.getActiveModel();
            const r = active.restore;
            // The model server is still starting; the saved model is checked once it answers.
            if (r?.status === 'pending' && restoreRetries < RESTORE_RETRIES) {
                restoreRetries += 1;
                setTimeout(() => { void get().refresh(); }, RESTORE_RETRY_MS);
            }
            set({
                active,
                error: null,
                ...(r && r.status !== 'none' && r.message
                    ? { restoreNotice: { ok: r.status === 'restored', message: r.message } }
                    : {}),
            });
        } catch (err) {
            set({ error: err instanceof Error ? err.message : String(err) });
        }
    },
    select: async (model) => {
        const active = await api.selectModel(model);
        set({ active, error: null, restoreNotice: null });
    },
    dismissRestoreNotice: () => set({ restoreNotice: null }),
}));

/** "Local" or the cloud provider's name, for the place-of-inference indicator. */
export function locationLabel(active: ActiveModel | null): string {
    if (!active) return 'Local';
    if (!active.remote) return 'Local';
    return active.provider === 'fireworks' ? 'Fireworks AI' : 'AMD LLM Gateway';
}

/** The model name without its provider prefix or catalogue path. */
export function shortModelName(model: string): string {
    const withoutProvider = /^(fireworks|amd)\./.test(model) ? model.slice(model.indexOf('.') + 1) : model;
    return withoutProvider.slice(withoutProvider.lastIndexOf('/') + 1);
}
