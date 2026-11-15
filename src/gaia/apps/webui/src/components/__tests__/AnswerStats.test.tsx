// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { AnswerStats, answerSummary, spokenDuration } from '../AnswerStats';
import { stripIsolates } from '../../i18n/registry';
import { translator } from '../../i18n/useT';
import { useChatStore } from '../../stores/chatStore';
import type { InferenceStats } from '../../types';

const stats: InferenceStats = {
    tokens_per_second: 38.1,
    time_to_first_token: 1.012,
    input_tokens: 68,
    output_tokens: 510,
    place: 'on your GPU',
};

describe('spokenDuration', () => {
    it.each([
        [400, 'under a second'],
        [1012, '1 s'],
        [40_200, '40 s'],
        [60_000, '1 min'],
        [95_000, '1 min 35 s'],
    ])('%i ms -> %s', (ms, words) => {
        expect(spokenDuration(ms)).toBe(words);
    });
});

describe('answerSummary', () => {
    it('says how long, where, and when the first word came', () => {
        expect(answerSummary(40_200, stats)).toBe('Answered in 40 s on your GPU · first word in 1 s');
    });

    it('names a cloud provider', () => {
        expect(answerSummary(5_000, { ...stats, place: 'via Fireworks AI' }))
            .toBe('Answered in 5 s via Fireworks AI · first word in 1 s');
    });

    it('makes no claim about where an older answer ran', () => {
        expect(answerSummary(40_200, { ...stats, place: undefined })).toBe('Answered in 40 s · first word in 1 s');
    });

    it('starts with the first word when the total time is unknown', () => {
        expect(answerSummary(undefined, { ...stats, place: undefined })).toBe('First word in 1 s');
    });

    it('is null with nothing measured', () => {
        expect(answerSummary(undefined, undefined)).toBeNull();
    });

    it('says a stopped turn stopped, not that it answered', () => {
        expect(answerSummary(16_000, stats, 'stopped')).toBe('Stopped after 16 s on your GPU · first word in 1 s');
    });

    it('says a cut-off turn was interrupted', () => {
        expect(answerSummary(16_000, undefined, 'interrupted')).toBe('Interrupted after 16 s');
    });

    it('claims no first word for a failed turn', () => {
        const cloud = { ...stats, time_to_first_token: 0.4, place: 'via Fireworks AI' };
        expect(answerSummary(1_000, cloud, 'failed')).toBe('Failed after 1 s via Fireworks AI');
        expect(answerSummary(undefined, { ...cloud, place: undefined }, 'failed')).toBeNull();
    });

    it('says only where an unfinished turn ran when its time is unknown', () => {
        const cloud = { ...stats, time_to_first_token: 0, place: 'via Fireworks AI' };
        expect(answerSummary(undefined, cloud, 'stopped')).toBe('Ran via Fireworks AI');
    });
});

describe('AnswerStats', () => {
    /** A footer line by its whole text; its numbers sit in their own `<bdi>`. */
    const line = (text: string) => (_: string, el: Element | null) => el?.tagName === 'SPAN' && el.textContent === text;

    it('shows plain words, and the exact numbers on a click', () => {
        render(<AnswerStats latencyMs={40_200} stats={stats} />);
        const summary = screen.getByRole('button', { name: 'Answered in 40 s on your GPU · first word in 1 s' });
        expect(summary).toHaveAttribute('title', expect.stringContaining('First token 1012 ms'));
        expect(screen.queryByText(line('68 → 510 tokens'))).not.toBeInTheDocument();
        expect(document.body).not.toHaveTextContent('TTFT');

        fireEvent.click(summary);
        expect(summary).toHaveAttribute('aria-expanded', 'true');
        expect(screen.getByText(line('Total time 40.2 s'))).toBeInTheDocument();
        expect(screen.getByText(line('First token 1012 ms'))).toBeInTheDocument();
        expect(screen.getByText(line('68 → 510 tokens'))).toBeInTheDocument();
        expect(screen.getByText(line('38.1 tokens/s'))).toBeInTheDocument();
    });

    it('leaves the first token out of a failed turn\'s exact numbers', () => {
        render(<AnswerStats latencyMs={1_000} stats={{ ...stats, place: 'via Fireworks AI' }} ended="failed" />);
        const summary = screen.getByRole('button', { name: 'Failed after 1 s via Fireworks AI' });
        expect(summary.getAttribute('title')).not.toContain('First token');
        fireEvent.click(summary);
        expect(screen.queryByText(line('First token 1012 ms'))).not.toBeInTheDocument();
        expect(screen.getByText(line('Total time 1.0 s'))).toBeInTheDocument();
    });

    it('renders nothing with nothing measured', () => {
        const { container } = render(<AnswerStats />);
        expect(container).toBeEmptyDOMElement();
    });
});

describe('AnswerStats in Arabic', () => {
    const ar = translator('ar');
    const read = (s: string | null | undefined) => (s == null ? s : stripIsolates(s));

    afterEach(() => useChatStore.setState({ locale: 'en' }));

    it('translates an answered turn, with the English in brackets', () => {
        expect(read(answerSummary(40_200, stats, undefined, ar))).toBe(
            'اكتملت الإجابة خلال 40 s على معالج الرسومات GPU · أول كلمة بعد 1 s'
            + ' (Answered in 40 s on your GPU · first word in 1 s)',
        );
    });

    it('translates a stopped cloud turn', () => {
        const cloud = { ...stats, time_to_first_token: 0, place: 'via Fireworks AI' };
        expect(read(answerSummary(13_000, cloud, 'stopped', ar)))
            .toBe('توقّفت بعد 13 s عبر Fireworks AI (Stopped after 13 s via Fireworks AI)');
    });

    it('translates an interrupted turn on this PC', () => {
        const local = { ...stats, time_to_first_token: 0, place: 'on this PC' };
        expect(read(answerSummary(16_000, local, 'interrupted', ar)))
            .toBe('انقطعت بعد 16 s على هذا الحاسوب (Interrupted after 16 s on this PC)');
    });

    it('translates where an unfinished turn ran, on two chips', () => {
        const both = { ...stats, time_to_first_token: 0, place: 'on your NPU and GPU' };
        expect(read(answerSummary(undefined, both, 'stopped', ar)))
            .toBe('جرى التشغيل على المعالج العصبي NPU ومعالج الرسومات GPU (Ran on your NPU and your GPU)');
    });

    it('translates "under a second" and the step count', () => {
        const quick = { ...stats, time_to_first_token: 0, place: undefined, steps: 3 };
        expect(read(answerSummary(400, quick, undefined, ar)))
            .toBe('اكتملت الإجابة خلال أقل من ثانية · الخطوات: 3 (Answered in under a second · 3 steps)');
        expect(spokenDuration(400, ar.bare)).toBe('أقل من ثانية');
    });

    it('keeps each duration left to right, and leaves no English outside the gloss', () => {
        useChatStore.setState({ locale: 'ar' });
        const { container } = render(<AnswerStats latencyMs={40_200} stats={stats} />);
        const summary = screen.getByRole('button');
        expect([...summary.querySelectorAll('bdi[dir="ltr"]')].map((b) => b.textContent))
            .toEqual(['40 s', '1 s', '40 s', '1 s']);
        expect(read(summary.querySelector('.i18n-gloss')?.textContent))
            .toBe('(Answered in 40 s on your GPU · first word in 1 s)');
        expect(summary.getAttribute('title')).not.toMatch(/[\uE000\uE001]/);

        fireEvent.click(summary);
        const lines = [...container.querySelectorAll(':scope > span')].slice(1).map((s) => read(s.textContent));
        expect(lines).toEqual([
            'الوقت الإجمالي 40.2 s (Total time 40.2 s)',
            'أول رمز بعد 1012 ms (First token 1012 ms)',
            'الرموز: 68 → 510 (68 → 510 tokens)',
            'رموز في الثانية: 38.1 (38.1 tokens/s)',
        ]);
    });
});
