// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';
import * as api from '../services/api';
import type { ModelNeed, ModelNeedJob } from '../types';

interface ModelNeededState {
    /** The prompt on screen, or null. */
    need: ModelNeed | null;
    /** Say whether *job* can run, asking to download its model first when it can't. */
    ensure: (job: ModelNeedJob) => Promise<boolean>;
    settle: (go: boolean) => void;
}

const CHECK_LIMIT_MS = 8000;

let answer: ((go: boolean) => void) | null = null;

/** Jobs already seen to have their model, so a send doesn't ask again. */
const ready = new Set<ModelNeedJob>();

export const modelKnownReady = (job: ModelNeedJob) => ready.has(job);

/** After a model is removed or the chat model changes. */
export const forgetModelChecks = () => ready.clear();

export const useModelNeededStore = create<ModelNeededState>((set) => ({
    need: null,
    ensure: async (job) => {
        let need: ModelNeed;
        try {
            need = await Promise.race([
                api.getModelNeed(job),
                new Promise<never>((_, reject) => {
                    setTimeout(() => reject(new Error('the check timed out')), CHECK_LIMIT_MS);
                }),
            ]);
        } catch (err) {
            // Unanswered isn't "missing": the job goes ahead and reports the real error.
            console.warn(`[modelNeeded] could not check the model for ${job}`, err);
            return true;
        }
        if (!need?.needed) {
            ready.add(job);
            return true;
        }
        return new Promise<boolean>((resolve) => {
            answer = resolve;
            set({ need });
        });
    },
    settle: (go) => {
        answer?.(go);
        answer = null;
        set({ need: null });
    },
}));

/** Callable outside React: a send or an upload waits on it. */
export const ensureModel = (job: ModelNeedJob) => useModelNeededStore.getState().ensure(job);
