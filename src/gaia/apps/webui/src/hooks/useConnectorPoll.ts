// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';
import { setVisibleInterval } from '../utils/visibleInterval';

const POLL_MS = 4000;
/** Ten minutes on screen: a sign-in left that long is abandoned, and window focus still re-reads. */
const POLL_LIMIT = 150;

/** Re-read the connectors every few seconds while `active`, giving up after ten minutes. */
export function useConnectorPoll(active: boolean, refresh: () => void): void {
    useEffect(() => {
        if (!active) return undefined;
        let left = POLL_LIMIT;
        const stop = setVisibleInterval(() => {
            refresh();
            left -= 1;
            if (left <= 0) stop();
        }, POLL_MS);
        return stop;
    }, [active, refresh]);
}
