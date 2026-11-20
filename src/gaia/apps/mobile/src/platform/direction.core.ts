// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Direction } from './direction.types';

export interface DirectionPlan {
    allowRTL: boolean;
    forceRTL: boolean;
}

// The flags persist, so they follow the explicit pick alone (null = follow the phone). React Native has no
// force-LTR: an English pick refuses RTL, so a later phone-language change can't flip the layout.
export function directionPlan(pick: Direction | null): DirectionPlan {
    if (pick === null) return { allowRTL: true, forceRTL: false };
    return pick === 'rtl' ? { allowRTL: true, forceRTL: true } : { allowRTL: false, forceRTL: false };
}
