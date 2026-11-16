// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { create } from 'zustand';

import type { SetupRunStatus } from '@/api/client.types';
import { useStartSetup } from '@/data';

interface QueuedModels {
    ids: string[];
    toggle(id: string): void;
    remove(ids: string[]): void;
}

/** Optional models added during the download; kept outside the step so they survive moving between steps. */
export const useQueuedModels = create<QueuedModels>((set) => ({
    ids: [],
    toggle: (id) => set((s) => ({ ids: s.ids.includes(id) ? s.ids.filter((x) => x !== id) : [...s.ids, id] })),
    remove: (ids) => set((s) => ({ ids: s.ids.filter((x) => !ids.includes(x)) })),
}));

/** Once the run has finished, starts a second run that fetches and tests what was added; returns its failure for the screen to say. */
export function useFlushQueuedModels(run: SetupRunStatus | null) {
    const ids = useQueuedModels((s) => s.ids);
    const remove = useQueuedModels((s) => s.remove);
    const start = useStartSetup();
    const sending = useRef(false);
    const ready = run != null && !run.server_only && run.state === 'ready';
    const { mutate } = start;
    useEffect(() => {
        if (!ready || ids.length === 0 || sending.current) return;
        sending.current = true;
        const extras = ids;
        mutate(
            { options: { extraModels: extras } },
            {
                onSuccess: () => remove(extras),
                onSettled: () => {
                    sending.current = false;
                },
            },
        );
    }, [ready, ids, mutate, remove]);
    return start.error;
}
