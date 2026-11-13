// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Whether the background goal loop can read the goal store. */

import { create } from 'zustand';
import * as memoryApi from '../services/memoryApi';
import { getT } from '../i18n/useT';
import { log } from '../utils/logger';
import { useNotificationStore } from './notificationStore';

interface GoalHealthState {
    /** Why approved goals can't run, as the backend reported it; null when they can. */
    error: string | null;
    check: () => Promise<void>;
}

let failures = 0;

export const useGoalHealthStore = create<GoalHealthState>((set, get) => ({
    error: null,

    check: async () => {
        let error: string | null;
        try {
            error = (await memoryApi.getGoalStoreHealth()).error;
        } catch (err) {
            // An unreachable backend is reported by the connection banner.
            log.api.error('Could not check the goal store', err);
            return;
        }
        // One notification per distinct failure, not one per poll.
        if (error && error !== get().error) {
            failures += 1;
            useNotificationStore.getState().addNotification({
                id: `goal-store-error-${failures}`,
                type: 'error',
                agentId: 'goals',
                agentName: 'GAIA',
                title: getT()('mem.goals.storeError'),
                message: error,
                timestamp: Date.now(),
                read: false,
                dismissed: false,
                priority: 'high',
            });
        }
        set({ error });
    },
}));
