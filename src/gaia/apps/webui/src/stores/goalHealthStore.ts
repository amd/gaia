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
    /** The goal list file is damaged, so only a new goal list fixes it. */
    damaged: boolean;
    check: () => Promise<void>;
}

const NOTIFICATION_ID = 'goal-store-error-';
let failures = 0;

/** A failure that has ended needs no one's attention: read its notifications. */
function readFailureNotifications() {
    const notes = useNotificationStore.getState();
    for (const n of notes.notifications) {
        if (n.id.startsWith(NOTIFICATION_ID) && !n.read) notes.markRead(n.id);
    }
}

export const useGoalHealthStore = create<GoalHealthState>((set, get) => ({
    error: null,
    damaged: false,

    check: async () => {
        let error: string | null;
        let damaged: boolean;
        try {
            ({ error, damaged } = await memoryApi.getGoalStoreHealth());
        } catch (err) {
            // An unreachable backend is reported by the connection banner.
            log.api.error('Could not check the goal store', err);
            return;
        }
        // One notification per distinct failure, not one per poll.
        if (error && error !== get().error) {
            failures += 1;
            useNotificationStore.getState().addNotification({
                id: `${NOTIFICATION_ID}${failures}`,
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
        if (!error) readFailureNotifications();
        set({ error, damaged: !!damaged });
    },
}));
