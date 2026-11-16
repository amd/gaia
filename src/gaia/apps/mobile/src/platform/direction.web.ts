// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ApplyDirection, LayoutDirection } from './direction.types';

export const applyDirection: ApplyDirection = (dir, lang) => {
    document.documentElement.dir = dir;
    document.documentElement.lang = lang;
};

export const layoutDirection: LayoutDirection = () => (document.documentElement.dir === 'rtl' ? 'rtl' : 'ltr');
