// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { middleTruncate } from '../format';

describe('middleTruncate', () => {
    it('leaves a short name alone', () => {
        expect(middleTruncate('notes.txt')).toBe('notes.txt');
    });

    it('keeps the start, the end and the extension of a long name', () => {
        const name = 'a_very_long_file_name_for_the_quarterly_review_v7_reviewed_by_dana.txt';
        const cut = middleTruncate(name);
        expect(cut).toHaveLength(32);
        expect(cut.startsWith('a_very_long_')).toBe(true);
        expect(cut.endsWith('by_dana.txt')).toBe(true);
        expect(cut).toContain('…');
    });

    it('keeps a long extension whole', () => {
        expect(middleTruncate(`${'x'.repeat(60)}.markdown`, 20).endsWith('x.markdown')).toBe(true);
    });
});
