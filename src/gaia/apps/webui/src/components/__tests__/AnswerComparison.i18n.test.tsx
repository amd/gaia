// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { AnswerComparison, type ComparisonState } from '../AnswerComparison';
import { stripIsolates } from '../../i18n/registry';
import { useChatStore } from '../../stores/chatStore';
import type { Message } from '../../types';

const original: Message = {
    id: 1,
    session_id: 's1',
    role: 'assistant',
    content: 'First answer',
    created_at: '2026-01-01T00:00:00Z',
    rag_sources: null,
    stats: { tokens_per_second: 31, time_to_first_token: 0.4, input_tokens: 10, output_tokens: 20 },
};

const ready: ComparisonState = {
    status: 'ready',
    result: {
        message_id: 2,
        model: 'Qwen3.6-35B-A3B-GGUF',
        content: 'Second answer',
        stats: {
            model: 'Qwen3.6-35B-A3B-GGUF',
            retried: true,
            seconds: 11.5,
            load_seconds: 4.2,
            input_tokens: 10,
            output_tokens: 20,
            tokens_per_second: 26.3,
            time_to_first_token: 0.5,
        },
    },
};

const ltrPieces = (el: Element) => [...el.querySelectorAll('bdi[dir="ltr"]')].map((b) => stripIsolates(b.textContent ?? ''));

describe('AnswerComparison in Arabic', () => {
    afterEach(() => useChatStore.setState({ locale: 'en' }));

    it('translates its labels and keeps each number with its unit', () => {
        useChatStore.setState({ locale: 'ar' });
        const { container } = render(
            <AnswerComparison
                original={original}
                originalModel="Gemma-4-E4B-it-GGUF"
                originalLatencyMs={8_000}
                state={ready}
                onKeep={() => {}}
                onClose={() => {}}
            />,
        );

        expect(stripIsolates(screen.getByRole('region').getAttribute('aria-label') ?? '')).toMatch(/^مقارنة الإجابات/);
        expect(screen.getAllByRole('button').map((b) => stripIsolates(b.textContent ?? '')))
            .toContain('الاحتفاظ بهذه الإجابة (Keep this answer)');

        const [first, second] = container.querySelectorAll('.compare-meta');
        expect(ltrPieces(first)).toEqual(['8.0 s', '31 tok/s']);
        expect(ltrPieces(second)).toEqual(['11.5 s', '4.2 s', '4.2 s', '26.3 tok/s']);
    });
});
