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
