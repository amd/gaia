// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef } from 'react';

/**
 * Keeps overlapping reads from landing out of order. Call the returned
 * function when a read starts; it hands back a check to call once, when the
 * read answers. The check is true unless a read started later has already
 * landed, or the component is gone.
 *
 * A read is not dropped merely because a newer one started: when reads repeat
 * faster than the server answers, each would retire the one before it and none
 * would ever land.
 *
 *     const begin = useLatestRead();
 *     const lands = begin();
 *     api.read().then((res) => { if (lands()) setState(res); });
 */
export function useLatestRead(): () => () => boolean {
    const started = useRef(0);
    const landed = useRef(0);
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; };
    }, []);
    return useCallback(() => {
        const mine = ++started.current;
        return () => {
            if (!mounted.current || mine < landed.current) return false;
            landed.current = mine;
            return true;
        };
    }, []);
}
