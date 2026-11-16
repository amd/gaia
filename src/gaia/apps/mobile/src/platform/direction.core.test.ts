// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { directionPlan } from './direction.core';

describe('directionPlan', () => {
    it.each([
        [null, { allowRTL: true, forceRTL: false }],
        ['ltr', { allowRTL: false, forceRTL: false }],
        ['rtl', { allowRTL: true, forceRTL: true }],
    ] as const)('pick %s', (pick, plan) => {
        expect(directionPlan(pick)).toEqual(plan);
    });
});
