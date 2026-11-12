// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// An answer's timestamp reads in the interface language, not the PC's.

import { afterEach, describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import { stripIsolates } from '../../i18n/registry';
import { ARABIC_MONTH, ENGLISH_MONTH, resetUiLocale, setUiLocale } from '../../test/dates';
import type { Message } from '../../types';

const answer: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'assistant',
    content: 'Done.',
    created_at: '2026-07-16T09:30:00.000Z',
    rag_sources: null,
};

afterEach(resetUiLocale);

describe('MessageBubble timestamp', () => {
    it('reads in Arabic in an Arabic UI, under the answer and in its hover text', () => {
        setUiLocale('ar');
        const { container } = render(<MessageBubble message={answer} />);
        const stamp = stripIsolates(container.querySelector('.msg-stats-ts')!.textContent ?? '');
        expect(stamp).toMatch(ARABIC_MONTH);
        expect(stamp).not.toMatch(ENGLISH_MONTH);
        const hover = [...container.querySelectorAll('[title]')]
            .map((el) => stripIsolates(el.getAttribute('title') ?? ''))
            .find((title) => ARABIC_MONTH.test(title) && title !== stamp);
        expect(hover).toBeDefined();
        expect(hover).not.toMatch(ENGLISH_MONTH);
    });

    it('stays in English in an English UI', () => {
        const { container } = render(<MessageBubble message={answer} />);
        expect(container.querySelector('.msg-stats-ts')!.textContent).not.toMatch(ARABIC_MONTH);
    });
});
