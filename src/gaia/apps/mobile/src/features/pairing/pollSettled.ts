// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaNetworkError } from '@/api/errors';

const NETWORK_CODES = new Set(['network', 'unreachable']);

/** Whether `error` is the network failing, which a later check may get past; anything else won't change by asking again. */
export function isNetworkFailure(error: unknown): boolean {
    return error instanceof GaiaNetworkError || NETWORK_CODES.has(String((error as { code?: unknown } | null)?.code));
}

/**
 * Runs `check` every `intervalMs`, the next only once the last has settled, so a check that outlasts the
 * interval never runs beside another. `answered` and `failed` say whether to check again. The returned
 * function stops it.
 */
export function pollSettled<T>(
    check: () => Promise<T>,
    intervalMs: number,
    answered: (result: T) => boolean,
    failed: (error: unknown) => boolean,
): () => void {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const next = () => {
        timer = setTimeout(() => {
            check()
                .then(answered, failed)
                .then((again) => {
                    if (again && !stopped) next();
                });
        }, intervalMs);
    };
    next();
    return () => {
        stopped = true;
        clearTimeout(timer);
    };
}
