// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { FORGOTTEN_QUOTE_CHARS, oneLineQuote } from '../MemoryDashboardAboutYou';

describe('the Undo bar quotes a forgotten memory on one line', () => {
    it('keeps a short memory whole', () => {
        expect(oneLineQuote('Lives in Calgary')).toBe('Lives in Calgary');
    });

    it('cuts a long memory with an ellipsis instead of wrapping to three lines', () => {
        const long = 'Prefers answers in metric units, short paragraphs, no emoji, and a summary first, then details, with sources linked at the end';
        const quoted = oneLineQuote(long);
        expect(quoted.length).toBeLessThanOrEqual(FORGOTTEN_QUOTE_CHARS);
        expect(quoted.endsWith('…')).toBe(true);
        expect(long.startsWith(quoted.slice(0, -1))).toBe(true);
    });

    it('folds line breaks into spaces', () => {
        expect(oneLineQuote('Line one\n\nline two')).toBe('Line one line two');
    });
});
