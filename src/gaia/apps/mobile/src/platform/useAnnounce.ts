// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';
import { AccessibilityInfo, Platform } from 'react-native';

/** Speaks `text` on iOS each time it changes; Android reads a live region on its own, so it gets the prop instead. */
export function useAnnounce(text: string | null | undefined): void {
    useEffect(() => {
        if (text && Platform.OS === 'ios') AccessibilityInfo.announceForAccessibility(text);
    }, [text]);
}
