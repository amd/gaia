// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';
import * as api from '../services/api';
import { log } from '../utils/logger';

interface ActiveAgentState {
    /** The saved agent choice (`gaiapy`, `gaiacpp`, or a name a newer GAIA wrote); null until read, or when the read failed. */
    current: string | null;
    /** The program running right now, which a terminal session can switch away from `current`; null when none runs. */
    running: string | null;
    /** Read the choice from the agent-programs listing. */
    refresh: () => Promise<void>;
    /** After this app saved a new choice. */
    setCurrent: (id: string) => void;
}

let reads = 0;

export const useActiveAgentStore = create<ActiveAgentState>((set) => ({
    current: null,
    running: null,
    refresh: async () => {
        const read = ++reads;
        try {
            const listing = await api.getAgentPrograms();
            if (read === reads) set({ current: listing.current, running: listing.running ?? null });
        } catch (err) {
            log.api.warn('Could not read which agent is in use', err);
            if (read === reads) set({ current: null, running: null });
        }
    },
    setCurrent: (id) => {
        reads++;
        // Saving a choice restarts the agent on it.
        set({ current: id, running: null });
    },
}));

/** The program that answers the next message: the one running, else the saved choice. */
export const answeringAgent = (s: Pick<ActiveAgentState, 'current' | 'running'>): string | null => s.running ?? s.current;
