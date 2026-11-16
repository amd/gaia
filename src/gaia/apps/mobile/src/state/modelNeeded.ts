// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

import type { GaiaClient, ModelNeed, ModelNeedJob } from '@/api/client.types';
import { MODEL_NEED_CHECK_LIMIT_MS } from '@/api/constants';

interface ModelNeededState {
    /** The prompt on screen, or null. */
    need: ModelNeed | null;
    /** Says whether `job` can run, asking to download its model first when it can't. */
    ensure(client: GaiaClient, job: ModelNeedJob): Promise<boolean>;
    settle(go: boolean): void;
}

/** Everyone waiting on the prompt on screen: one answer releases them all. */
let waiting: ((go: boolean) => void)[] = [];

/** Jobs already seen to have their model, so a send doesn't ask again. */
const ready = new Set<ModelNeedJob>();

export const forgetModelChecks = () => ready.clear();

export const useModelNeededStore = create<ModelNeededState>((set, get) => ({
    need: null,
    ensure: async (client, job) => {
        if (ready.has(job)) return true;
        const open = get().need;
        if (open) {
            // One prompt at a time: the same job waits on its answer, another job doesn't run.
            if (open.job !== job) return false;
            return new Promise<boolean>((resolve) => waiting.push(resolve));
        }
        const controller = new AbortController();
        const limit = setTimeout(() => controller.abort(), MODEL_NEED_CHECK_LIMIT_MS);
        let need: ModelNeed;
        try {
            need = await client.getModelNeed(job, { signal: controller.signal });
        } catch (err) {
            // Unanswered isn't "missing": the job goes ahead and reports the real error.
            console.warn(`[modelNeeded] could not check the model for ${job}`, err);
            return true;
        } finally {
            clearTimeout(limit);
        }
        // Off on this PC: asked every time, so the person always knows it isn't read.
        if (!need.needed && !need.off) {
            ready.add(job);
            return true;
        }
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
