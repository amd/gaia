// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
import { I18nManager } from 'react-native';

const RTL_LETTER = /[֐-ࣿיִ-﷿ﹰ-﻿]/;
const LETTER = /\p{L}/u;

/** A block's direction from its first strong letter, like the web UI's dir="auto". */
export function contentDir(text: string, fallback: 'ltr' | 'rtl'): 'ltr' | 'rtl' {
    for (const ch of text) {
        if (!LETTER.test(ch)) continue;
        return RTL_LETTER.test(ch) ? 'rtl' : 'ltr';
    }
    return fallback;
}

/** The physical `textAlign` that lands on the right or left edge: native flips left and right in an RTL layout, web doesn't. */
export function physicalAlign(right: boolean): 'left' | 'right' {
    const native = I18nManager.getConstants();
    const mirrored = native.isRTL && !!native.doLeftAndRightSwapInRTL;
    return right !== mirrored ? 'right' : 'left';
}
