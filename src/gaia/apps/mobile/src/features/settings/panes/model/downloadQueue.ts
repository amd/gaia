// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

interface DownloadQueueState {
    /** Models waiting for the one before them: the PC pulls one at a time. */
    queue: { id: string; acknowledgeLowDisk: boolean }[];
    /** Every model started from here, so the panel shows these and not old finished rows. */
    started: string[];
    enqueue(ids: string[], acknowledgeLowDisk: boolean): void;
    shift(): { id: string; acknowledgeLowDisk: boolean } | undefined;
    clear(): void;
}

export const useDownloadQueue = create<DownloadQueueState>((set, get) => ({
    queue: [],
    started: [],
    enqueue: (ids, acknowledgeLowDisk) =>
        set((s) => ({
            queue: [...s.queue.filter((q) => !ids.includes(q.id)), ...ids.map((id) => ({ id, acknowledgeLowDisk }))],
            started: [...new Set([...s.started, ...ids])],
        })),
    shift: () => {
        const [head, ...rest] = get().queue;
        if (head) set({ queue: rest });
        return head;
    },
    clear: () => set({ queue: [] }),
}));
