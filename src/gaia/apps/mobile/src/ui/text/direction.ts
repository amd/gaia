// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

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
