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
            'Waiting for the GPU — 3rd in line',
            'Waiting for the GPU — 11th in line',
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

    it('localizes the skill reload warning while keeping the skill name', () => {
        const label = "Skill 'broken' was not restored after settings changed: its files could not be found. Reload it if needed.";
        expect(localizeLiveStatus(tEn, label)).toBe(label);
        expect(ar(label)).toContain('لم تُستعد المهارة broken');
        expect(ar(label)).toContain('تعذّر العثور على ملفاتها');
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

    it('translates the GPU queue line for Arabic, with a plain number', () => {
        expect(ar('Waiting for the GPU — 3rd in line')).toBe('بانتظار معالج الرسومات — رقم 3 في الدور (Waiting for the GPU — 3rd in line)');
    });

    it('says the speech model download in the chosen language, with its percent and size', () => {
        const running = 'Downloading the speech model, one time — 37% of 1.6 GB';
        expect(localizeLiveStatus(tEn, running)).toBe(running);
        expect(localizeLiveStatus(tEn, 'Downloading the speech model, one time (1.6 GB)'))
            .toBe('Downloading the speech model, one time (1.6 GB)');
        expect(localizeLiveStatus(tEn, 'Downloaded the speech model (1.6 GB)')).toBe('Downloaded the speech model (1.6 GB)');
        expect(ar(running)).toContain('37%');
        expect(ar(running)).toContain('1.6');
        expect(ar(running)).toContain('نموذج الكلام');
    });

    it('says the speech model download in the chosen language when no size is known', () => {
        const lines = [
            'Downloading the speech model, one time',
            'Downloading the speech model, one time — 37%',
            'Downloaded the speech model',
        ];
        for (const label of lines) {
            expect(localizeLiveStatus(tEn, label)).toBe(label);
            expect(ar(label)).toContain('نموذج الكلام');
        }
        expect(ar(lines[1])).toContain('37%');
    });

    it('says the NPU part of the speech model in the chosen language', () => {
        const percent = 'Downloading the speech model, one time — 37% of 1.6 GB, then 0.7 GB for the NPU';
        const npu = "Downloading the speech model's NPU part (0.7 GB), no progress shown";
        const npuNoSize = "Downloading the speech model's NPU part, no progress shown";
        for (const label of [percent, npu, npuNoSize, 'Loading the speech model']) {
            expect(localizeLiveStatus(tEn, label)).toBe(label);
            expect(ar(label)).toContain('نموذج الكلام');
        }
        expect(ar(percent)).toContain('37%');
        expect(ar(percent)).toContain('0.7');
        expect(ar(npu)).toContain('0.7');
        // No percent to show for the NPU part, so it says why instead of a bare timer.
        expect(ar(npu)).toContain('دون عرض للتقدّم');
    });

    it('says a call Full access or Accept edits ran without asking in the chosen language, keeping the action', () => {
        const full = "Full access is on, so GAIA didn't ask first. Writing file: notes.txt";
        const edits = "Accept edits is on, so GAIA didn't ask first. Editing file: notes.txt";
        const unknown = "Full access is on, so GAIA didn't ask first. Frobbing the widget";
        for (const label of [full, edits, unknown]) expect(localizeLiveStatus(tEn, label)).toBe(label);
        expect(ar(full)).toBe(
            "الوصول الكامل مفعّل، لذا لم يسأل GAIA أولًا. كتابة ملف: notes.txt (Full access is on, so GAIA didn't ask first. Writing file: notes.txt)",
        );
        expect(ar(edits)).toBe(
            "قبول التعديلات مفعّل، لذا لم يسأل GAIA أولًا. تحرير ملف: notes.txt (Accept edits is on, so GAIA didn't ask first. Editing file: notes.txt)",
        );
        // A narration with no tool label keeps its own words, inside the translated line.
        expect(ar(unknown)).toContain('الوصول الكامل مفعّل، لذا لم يسأل GAIA أولًا. Frobbing the widget');
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
