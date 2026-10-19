// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AnswerStats, answerSummary, spokenDuration } from '../AnswerStats';
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
});

describe('AnswerStats', () => {
    it('shows plain words, and the exact numbers on a click', () => {
        render(<AnswerStats latencyMs={40_200} stats={stats} />);
        const summary = screen.getByRole('button', { name: 'Answered in 40 s on your GPU · first word in 1 s' });
        expect(summary).toHaveAttribute('title', expect.stringContaining('First token 1012 ms'));
        expect(screen.queryByText('68 → 510 tokens')).not.toBeInTheDocument();
        expect(document.body).not.toHaveTextContent('TTFT');

        fireEvent.click(summary);
        expect(summary).toHaveAttribute('aria-expanded', 'true');
        expect(screen.getByText('Total time 40.2 s')).toBeInTheDocument();
        expect(screen.getByText('First token 1012 ms')).toBeInTheDocument();
        expect(screen.getByText('68 → 510 tokens')).toBeInTheDocument();
        expect(screen.getByText('38.1 tokens/s')).toBeInTheDocument();
    });

    it('renders nothing with nothing measured', () => {
        const { container } = render(<AnswerStats />);
        expect(container).toBeEmptyDOMElement();
    });
});
