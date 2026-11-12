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

    it('never splits an emoji into half a character', () => {
        const cut = middleTruncate(`${'\u{1F600}'.repeat(30)}.png`);
        expect(cut).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/);
        expect(Array.from(cut)).toHaveLength(32);
        expect(cut.endsWith('.png')).toBe(true);
    });

    it('stays within a small limit, even with a long extension', () => {
        const cut = middleTruncate(`${'x'.repeat(40)}.markdown`, 12);
        expect(cut).toHaveLength(12);
        expect(cut.startsWith('x')).toBe(true);
        expect(cut).toContain('…');
    });

    it('leaves a name exactly at the limit alone', () => {
        expect(middleTruncate('a'.repeat(32))).toBe('a'.repeat(32));
    });
});
