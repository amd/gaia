// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { AppState } from 'react-native';

function secondsLeft(deadline: number): number {
    return Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
}

/**
 * Whole seconds until a prompt's deadline, or null when it has none. The deadline is fixed
 * when the card mounts (`expiresAt`, else `timeoutSeconds` from now); the tick only reads it,
 * and pauses in the background so it is recomputed, not counted, when the app returns.
 */
export function useCountdown(expiresAt: number | undefined, timeoutSeconds: number | undefined): number | null {
    const [end] = useState<number | null>(
        () => expiresAt ?? (timeoutSeconds != null && timeoutSeconds > 0 ? Date.now() + timeoutSeconds * 1000 : null),
    );
    const [left, setLeft] = useState<number | null>(() => (end === null ? null : secondsLeft(end)));

    useEffect(() => {
        if (end === null) return undefined;
        let timer: ReturnType<typeof setInterval> | undefined;
        const stop = () => {
            if (timer !== undefined) clearInterval(timer);
            timer = undefined;
        };
        const tick = () => {
            const next = secondsLeft(end);
            setLeft(next);
            if (next === 0) stop();
        };
        const start = () => {
            stop();
            tick();
            if (secondsLeft(end) > 0) timer = setInterval(tick, 1000);
        };
        if (AppState.currentState !== 'background') start();
        const sub = AppState.addEventListener('change', (state) => {
            if (state === 'active') start();
            else if (state === 'background') stop();
        });
        return () => {
            stop();
            sub.remove();
        };
    }, [end]);

    return left;
}
