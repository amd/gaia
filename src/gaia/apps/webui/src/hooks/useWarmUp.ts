// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import * as api from '../services/api';
import type { WarmUpStatus } from '../types';
import { log } from '../utils/logger';

const POLL_MS = 1_000;

/**
 * Prime the backend for this new chat's first turn and report progress.
 *
 * Returns the seconds the warm-up has been running, or null when it is not
 * running. Never blocks sending: a message sent mid-warm-up waits for it
 * server-side instead of reading the prompt twice.
 */
export function useWarmUp(): number | null {
    const [elapsed, setElapsed] = useState<number | null>(null);

    useEffect(() => {
        let stopped = false;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const show = (status: WarmUpStatus) => {
            if (stopped) return;
            if (status.state !== 'warming') {
                setElapsed(null);
                return;
            }
            setElapsed(Math.floor(status.elapsed_seconds ?? 0));
            timer = setTimeout(() => {
                api.getWarmUp().then(show, fail);
            }, POLL_MS);
        };
        const fail = (err: unknown) => {
            if (stopped) return;
            log.system.warn('Warm-up status unavailable', err);
            setElapsed(null);
        };
        api.requestWarmUp().then(show, fail);
        return () => {
            stopped = true;
            if (timer) clearTimeout(timer);
        };
    }, []);

    return elapsed;
}
