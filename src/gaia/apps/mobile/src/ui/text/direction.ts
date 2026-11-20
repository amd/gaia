// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { I18nManager } from 'react-native';

const RTL_LETTER = /[֐-ࣿיִ-﷿ﹰ-﻿]/;
const LETTER = /\p{L}/u;

const ISOLATE_OPEN = /[\u2066-\u2068]/;

function firstStrong(text: string, skipIsolates: boolean): 'ltr' | 'rtl' | null {
    let depth = 0;
    for (const ch of text) {
        if (skipIsolates) {
            if (ISOLATE_OPEN.test(ch)) depth++;
            else if (ch === '\u2069') {
                if (depth > 0) depth--;
            }
            if (depth > 0) continue;
        }
        if (!LETTER.test(ch)) continue;
        return RTL_LETTER.test(ch) ? 'rtl' : 'ltr';
    }
    return null;
}

/** A block's direction from its first strong letter outside any isolate (Unicode P2), like the web UI's dir="auto". */
export function contentDir(text: string, fallback: 'ltr' | 'rtl'): 'ltr' | 'rtl' {
    return firstStrong(text, true) ?? fallback;
}

/** Whether an isolate before the first strong letter makes the browser's own guess differ from `contentDir`. */
export function isolateMisleads(text: string, fallback: 'ltr' | 'rtl'): boolean {
    return (firstStrong(text, false) ?? fallback) !== contentDir(text, fallback);
}

/** The physical `textAlign` that lands on the right or left edge: native flips left and right in an RTL layout, web doesn't. */
export function physicalAlign(right: boolean): 'left' | 'right' {
    const native = I18nManager.getConstants();
    const mirrored = native.isRTL && !!native.doLeftAndRightSwapInRTL;
    return right !== mirrored ? 'right' : 'left';
}
