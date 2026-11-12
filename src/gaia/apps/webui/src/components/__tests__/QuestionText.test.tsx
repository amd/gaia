// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The question card shows the model's Markdown as formatting, not as raw
 * asterisks and dashes, and still never turns question text into a link, an
 * image or markup.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { NeedsInputCard } from '../render/NeedsInputCard';
import { QuestionText } from '../render/QuestionText';
import { useChatStore } from '../../stores/chatStore';

vi.mock('../../services/api');

const MARKDOWN_QUESTION =
    'I found these in the folder:\n\n' +
    '- **4 invoices** (txt): keep or archive?\n' +
    '- **4 meeting notes** (md): merge into `notes.md`?\n\n' +
    'What should I do with the *duplicates*?';

const card = (question: string) => ({
    session_id: 'session-1',
    request_id: 'req-1',
    question,
    options: [],
    allow_free_text: true,
    sensitive: false,
});

beforeEach(() => {
    useChatStore.setState({ liveQuestions: { 'req-1': { sessionId: 'session-1', expiresAt: null } } });
});

describe('QuestionText', () => {
    it('shows bold, lists, italics and code as formatting', () => {
        const { container } = render(<QuestionText text={MARKDOWN_QUESTION} />);
        expect(container.textContent).not.toMatch(/\*|`/);
        expect(container.querySelectorAll('li')).toHaveLength(2);
        expect(container.querySelector('strong')?.textContent).toBe('4 invoices');
        expect(container.querySelector('em')?.textContent).toBe('duplicates');
        expect(container.querySelector('code')?.textContent).toBe('notes.md');
    });

    it('leaves a plain question as it was written, line breaks included', () => {
        const { container } = render(<QuestionText text={'Which mailbox?\nGmail or Outlook'} />);
        expect(screen.getByText(/Which mailbox\?/)).toBeInTheDocument();
        expect(container.querySelector('p')?.textContent).toBe('Which mailbox?\nGmail or Outlook');
    });

    it('never makes a link, an image, a heading or markup out of question text', () => {
        const { container } = render(
            <QuestionText
                text={
                    '# Urgent\n\nOpen [the form](https://example.com/login) ![x](https://example.com/p.png)' +
                    ' <img src=x onerror=alert(1)> <script>alert(1)</script>'
                }
            />,
        );
        expect(container.querySelector('a, img, h1, script')).toBeNull();
        expect(container.textContent).toContain('the form');
        expect(container.textContent).toContain('<script>alert(1)</script>');
    });
});

describe('NeedsInputCard — Markdown in the question', () => {
    it('formats a waiting question', () => {
        const { container } = render(<NeedsInputCard data={card(MARKDOWN_QUESTION)} />);
        const question = container.querySelector('.render-needs-input__question');
        expect(question?.querySelector('strong')?.textContent).toBe('4 invoices');
        expect(question?.textContent).not.toContain('**');
    });

    it('formats a question that is no longer waiting', () => {
        useChatStore.setState({ liveQuestions: {} });
        const { container } = render(<NeedsInputCard data={card(MARKDOWN_QUESTION)} />);
        const question = container.querySelector('.render-needs-input--expired .render-needs-input__question');
        expect(question?.querySelector('strong')?.textContent).toBe('4 invoices');
        expect(question?.textContent).not.toContain('**');
    });
});
