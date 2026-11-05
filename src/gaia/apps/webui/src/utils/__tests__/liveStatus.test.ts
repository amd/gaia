// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The live status line under a question reads in the chosen language. */

import { describe, expect, it } from 'vitest';
import { stripIsolates } from '../../i18n/registry';
import { tEn, translator } from '../../i18n/useT';
import { localizeLiveStatus } from '../liveStatus';
import { SEND_WAITING_NOTICE_TEXT } from '../../services/api';

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
            'The model server stopped. GAIA is restarting it…',
            'Starting the model server…',
            'Reading page 4 of 12…',
        ]) {
            expect(localizeLiveStatus(tEn, label)).toBe(label);
        }
    });

    it('translates the reading line for Arabic', () => {
        expect(ar('Reading your request')).toBe('جارٍ قراءة طلبك (Reading your request)');
    });

    it('translates the model-server restart line for Arabic', () => {
        expect(ar('The model server stopped. GAIA is restarting it…')).toContain('توقف خادم النموذج');
    });

    it('keeps counts, attempts and model names in the Arabic line', () => {
        expect(ar('Reasoning — 1,234 words so far')).toContain('الكلمات حتى الآن: 1,234');
        expect(ar('Connection lost. Reconnecting (attempt 2 of 3)…')).toContain('المحاولة 2 من 3');
        expect(ar('Loading Gemma-4-E4B-it-GGUF into memory')).toContain('جارٍ تحميل Gemma-4-E4B-it-GGUF');
    });

    it('keeps the page counts in the Arabic extraction progress line', () => {
        expect(ar('Reading page 4 of 12…')).toContain('4');
        expect(ar('Reading page 4 of 12…')).toContain('12');
        expect(ar('Reading page 4 of 12…')).not.toBe('Reading page 4 of 12…');
    });

    it('shows a line it does not know as written', () => {
        for (const label of [
            'Writing file — 120 characters so far',
            'Downloading file — 120 characters so far',
            "Downloading the embedding model 'nomic-embed-text-v2-moe-GGUF' for memory: 40%",
            'Downloading Whisper-Large-v3-Turbo through Lemonade (~1.60 GB)',
            'The model stopped sending — retrying now (1/1)',
        ]) {
            expect(ar(label)).toBe(label);
        }
    });

    it('recognises the lines the UI itself writes', () => {
        expect(ar(SEND_WAITING_NOTICE_TEXT)).toContain('بانتظار أن يبدأ GAIA');
    });
});
