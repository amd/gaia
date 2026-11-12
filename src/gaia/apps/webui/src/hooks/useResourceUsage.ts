// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { getResourceUsage } from '../services/api';
import type { ResourceUsage } from '../types';
import { log } from '../utils/logger';

/** How long to wait after a failed read before trying again. */
export const RETRY_MS = 5000;

export interface ResourceUsageState {
    usage: ResourceUsage | null;
    error: string | null;
}

/**
 * Polls this PC's resource usage while `enabled` and the window is visible.
 * The backend keeps the history, so the graphs survive a pause shorter than
 * its 15 s idle stop; after a longer one they start over.
 */
export function useResourceUsage(enabled: boolean): ResourceUsageState {
    const [state, setState] = useState<ResourceUsageState>({ usage: null, error: null });

    useEffect(() => {
        if (!enabled) return;
        let timer: ReturnType<typeof setTimeout> | undefined;
        let controller: AbortController | null = null;
        let stopped = false;

        const schedule = (ms: number) => {
            if (stopped || document.hidden) return;
            timer = setTimeout(tick, ms);
        };

        async function tick() {
            controller = new AbortController();
            try {
                const usage = await getResourceUsage(controller.signal);
                if (stopped) return;
                setState({ usage, error: null });
                schedule(usage.interval_ms);
            } catch (err) {
                if (stopped || controller.signal.aborted) return;
                log.ui.warn('Could not read resource usage', err);
                setState((s) => ({ usage: s.usage, error: err instanceof Error ? err.message : String(err) }));
                schedule(RETRY_MS);
            }
        }

        const onVisibility = () => {
            if (document.hidden) {
                clearTimeout(timer);
                controller?.abort();
            } else {
                clearTimeout(timer);
                void tick();
            }
        };

        void tick();
        document.addEventListener('visibilitychange', onVisibility);
        return () => {
            stopped = true;
            clearTimeout(timer);
            controller?.abort();
            document.removeEventListener('visibilitychange', onVisibility);
        };
    }, [enabled]);

    return enabled ? state : { usage: null, error: null };
}
