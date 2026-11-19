// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Keep asking until it works. The desktop app opens its window before the
 * backend listens, so a request made at mount can fail for the first seconds;
 * one try and give up leaves the UI on placeholders for the whole session.
 */

export const RETRY_INITIAL_MS = 1_000;
export const RETRY_MAX_MS = 30_000;

export interface Retrier {
    /** Try again now instead of waiting out the backoff (e.g. the backend just came back). */
    kick: () => void;
    /** Stop retrying; a try already in flight is ignored. */
    stop: () => void;
    /** Whether a try has succeeded. */
    done: () => boolean;
}

export function retryUntilOk(
    task: () => Promise<void>,
    {
        initialMs = RETRY_INITIAL_MS,
        maxMs = RETRY_MAX_MS,
        onError,
    }: { initialMs?: number; maxMs?: number; onError?: (err: unknown, attempt: number) => void } = {},
): Retrier {
    let delay = initialMs;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let running = false;
    let succeeded = false;
    let stopped = false;

    const run = () => {
        if (stopped || succeeded || running) return;
        if (timer) { clearTimeout(timer); timer = null; }
        running = true;
        attempt += 1;
        task()
            .then(() => { succeeded = true; })
            .catch((err) => {
                if (stopped) return;
                onError?.(err, attempt);
                timer = setTimeout(() => { timer = null; run(); }, delay);
                delay = Math.min(delay * 2, maxMs);
            })
            .finally(() => { running = false; });
    };

    run();
    return {
        kick: () => { if (timer) run(); },
        stop: () => {
            stopped = true;
            if (timer) clearTimeout(timer);
            timer = null;
        },
        done: () => succeeded,
    };
}
