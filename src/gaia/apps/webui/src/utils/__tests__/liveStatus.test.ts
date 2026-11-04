// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The live status line under a question reads in the chosen language. */

import { describe, expect, it } from 'vitest';
import { stripIsolates } from '../../i18n/registry';
import { tEn, translator } from '../../i18n/useT';
import { localizeLiveStatus } from '../liveStatus';

const ar = (label: string) => stripIsolates(localizeLiveStatus(translator('ar'), label));

describe('localizeLiveStatus', () => {
    it('leaves every known line unchanged in English', () => {
        for (const label of [
            'Reading your request',
            'Reading the tool results',
            'Reasoning',
            'Reasoning — 1,234 words so far',
            'Loading Gemma-4-E4B-it-GGUF into memory',
            'Downloading Gemma-4-E4B-it-GGUF',
            'Reconnecting to the model server…',
            'Connection lost. Reconnecting (attempt 2 of 3)…',
            'Waiting for GAIA to start this answer…',
        ]) {
            expect(localizeLiveStatus(tEn, label)).toBe(label);
        }
    });

    it('translates the reading line for Arabic', () => {
        expect(ar('Reading your request')).toBe('جارٍ قراءة طلبك (Reading your request)');
    });

    it('keeps counts, attempts and model names in the Arabic line', () => {
        expect(ar('Reasoning — 1,234 words so far')).toContain('الكلمات حتى الآن: 1,234');
        expect(ar('Connection lost. Reconnecting (attempt 2 of 3)…')).toContain('المحاولة 2 من 3');
        expect(ar('Loading Gemma-4-E4B-it-GGUF into memory')).toContain('جارٍ تحميل Gemma-4-E4B-it-GGUF');
    });

    it('shows a line it does not know as written', () => {
        expect(ar('Writing file — 120 characters so far')).toBe('Writing file — 120 characters so far');
        expect(ar('Downloading file — 120 characters so far')).toBe('Downloading file — 120 characters so far');
    });
});
