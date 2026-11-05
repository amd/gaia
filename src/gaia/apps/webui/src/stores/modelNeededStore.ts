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

/** Everyone waiting on the prompt on screen: one answer releases them all. */
let waiting: ((go: boolean) => void)[] = [];

/** Jobs already seen to have their model, so a send doesn't ask again. */
const ready = new Set<ModelNeedJob>();

export const modelKnownReady = (job: ModelNeedJob) => ready.has(job);

/** After a model is removed or the chat model changes. */
export const forgetModelChecks = () => ready.clear();

export const useModelNeededStore = create<ModelNeededState>((set, get) => ({
    need: null,
    ensure: async (job) => {
        if (ready.has(job)) return true;
        const open = get().need;
        if (open) {
            // One prompt at a time: the same job waits on its answer, another job doesn't run.
            if (open.job !== job) return false;
            return new Promise<boolean>((resolve) => { waiting.push(resolve); });
        }
        let need: ModelNeed;
        let limit: ReturnType<typeof setTimeout> | undefined;
        try {
            need = await Promise.race([
                api.getModelNeed(job),
                new Promise<never>((_, reject) => {
                    limit = setTimeout(() => reject(new Error('the check timed out')), CHECK_LIMIT_MS);
                }),
            ]);
        } catch (err) {
            // Unanswered isn't "missing": the job goes ahead and reports the real error.
            console.warn(`[modelNeeded] could not check the model for ${job}`, err);
            return true;
        } finally {
            clearTimeout(limit);
        }
        // Off on this PC: asked every time, so the user always knows it isn't read.
        if (!need?.needed && !need?.off) {
            ready.add(job);
            return true;
        }
        // Another check may have opened a prompt while this one was in flight.
        const opened = get().need;
        if (opened && opened.job !== job) return false;
        return new Promise<boolean>((resolve) => {
            waiting.push(resolve);
            if (!opened) set({ need });
        });
    },
    settle: (go) => {
        const answered = waiting;
        waiting = [];
        set({ need: null });
        for (const resolve of answered) resolve(go);
    },
}));

/** Callable outside React: a send or an upload waits on it. */
export const ensureModel = (job: ModelNeedJob) => useModelNeededStore.getState().ensure(job);
