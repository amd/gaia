// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { I18nManager } from 'react-native';

import { directionPlan } from './direction.core';
import type { ApplyDirection, LayoutDirection } from './direction.types';

// Native layout only flips after an app restart. Both flags persist, so every launch rewrites them.
export const applyDirection: ApplyDirection = (dir, _lang, picked) => {
    const plan = directionPlan(picked ? dir : null);
    I18nManager.allowRTL(plan.allowRTL);
    I18nManager.forceRTL(plan.forceRTL);
};

export const layoutDirection: LayoutDirection = () => (I18nManager.isRTL ? 'rtl' : 'ltr');
