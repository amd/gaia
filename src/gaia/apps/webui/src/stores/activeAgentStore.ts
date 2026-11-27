// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';
import * as api from '../services/api';
import { log } from '../utils/logger';

interface ActiveAgentState {
    /** The saved agent choice (`gaiapy`, `gaiacpp`, or a name a newer GAIA wrote); null until read, or when the read failed. */
    current: string | null;
    /** Read the choice from the agent-programs listing. */
    refresh: () => Promise<void>;
    /** After this app saved a new choice. */
    setCurrent: (id: string) => void;
}

let reads = 0;

export const useActiveAgentStore = create<ActiveAgentState>((set) => ({
    current: null,
    refresh: async () => {
        const read = ++reads;
        try {
            const listing = await api.getAgentPrograms();
            if (read === reads) set({ current: listing.current });
        } catch (err) {
            log.api.warn('Could not read which agent is in use', err);
            if (read === reads) set({ current: null });
        }
    },
    setCurrent: (id) => {
        reads++;
        set({ current: id });
    },
}));
