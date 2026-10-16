// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import * as api from '../services/api';
import type { WarmUpStatus } from '../types';
import { log } from '../utils/logger';

const POLL_MS = 1_000;

export interface WarmUpProgress {
    seconds: number;
    /** What is being loaded, once the backend has resolved it. */
    model: string | null;
    device: WarmUpStatus['device'];
}

/**
 * Prime the backend for this new chat's first turn and report progress.
 *
 * Waits for *enabled*: the backend refuses a warm-up until it has seen setup
 * finished. Returns what is loading and for how long, or null when nothing is. Never
 * blocks sending: a message sent mid-warm-up waits for it server-side instead
 * of reading the prompt twice.
 */
export function useWarmUp(enabled = true): WarmUpProgress | null {
    const [progress, setProgress] = useState<WarmUpProgress | null>(null);

    useEffect(() => {
        if (!enabled) return;
        let stopped = false;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const show = (status: WarmUpStatus) => {
            if (stopped) return;
            if (status.state !== 'warming') {
                setProgress(null);
                return;
            }
            setProgress({
                seconds: Math.floor(status.elapsed_seconds ?? 0),
                model: status.model ?? null,
                device: status.device ?? null,
            });
            timer = setTimeout(() => {
                api.getWarmUp().then(show, fail);
            }, POLL_MS);
        };
        const fail = (err: unknown) => {
            if (stopped) return;
            log.system.warn('Warm-up status unavailable', err);
            setProgress(null);
        };
        api.requestWarmUp().then(show, fail);
        return () => {
            stopped = true;
            if (timer) clearTimeout(timer);
        };
    }, [enabled]);

    return progress;
}
