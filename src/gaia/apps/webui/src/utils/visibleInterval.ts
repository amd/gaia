// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * `setInterval` that skips ticks while the window is hidden, and runs once as
 * soon as it is shown again. For polls nobody can see; never for the ones that
 * drive notifications while the user is in another app.
 *
 * Returns the function that stops it.
 */
export function setVisibleInterval(fn: () => void, ms: number): () => void {
    const hidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden';
    const timer = setInterval(() => { if (!hidden()) fn(); }, ms);
    const onShow = () => { if (!hidden()) fn(); };
    document.addEventListener('visibilitychange', onShow);
    return () => {
        clearInterval(timer);
        document.removeEventListener('visibilitychange', onShow);
    };
}

/**
 * Run a self-scheduling poll only while the window is shown: `start` when it
 * appears, its returned stop when it hides. Returns the function that ends it.
 */
export function runWhileVisible(start: () => () => void): () => void {
    const hidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden';
    let stop: (() => void) | null = hidden() ? null : start();
    const onChange = () => {
        if (hidden()) {
            stop?.();
            stop = null;
        } else if (!stop) {
            stop = start();
        }
    };
    document.addEventListener('visibilitychange', onChange);
    return () => {
        document.removeEventListener('visibilitychange', onChange);
        stop?.();
        stop = null;
    };
}
