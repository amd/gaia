// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { BackHandler } from 'react-native';

/**
 * Android's system back for a step flow: `step` goes back one step and returns true, or returns false to let back pop
 * the screen. Registered once, on mount, so a sheet opened later still closes first (Android asks the newest handler first).
 */
export function useBackStep(step: () => boolean) {
    const latest = useRef(step);
    useEffect(() => {
        latest.current = step;
    });
    useEffect(() => {
        const sub = BackHandler.addEventListener('hardwareBackPress', () => latest.current());
        return () => sub.remove();
    }, []);
}
