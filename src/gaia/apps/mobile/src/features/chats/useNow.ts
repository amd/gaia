// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { AppState } from 'react-native';

import { msUntilNextMidnight } from './chatList';

/** The current time, refreshed at local midnight and when the app returns to the foreground. */
export function useNow(): Date {
    const [now, setNow] = useState(() => new Date());
    useEffect(() => {
        const timer = setTimeout(() => setNow(new Date()), msUntilNextMidnight(now));
        return () => clearTimeout(timer);
    }, [now]);
    useEffect(() => {
        const sub = AppState.addEventListener('change', (state) => {
            if (state === 'active') setNow(new Date());
        });
        return () => sub.remove();
    }, []);
    return now;
}
