// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Each block of an answer picks its own direction, so a mixed Arabic/English
// answer reads right to left where it is Arabic; code always reads left to right.

import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { MessageBubble, RenderedContent } from '../MessageBubble';
import { markStrayDirection } from '../CalendarApproval';
import type { Message } from '../../types';

const ANSWER = [
    '## ملخص التقرير',
    '',
    'يغطي التقرير الربع الثالث.',
    '',
    'The report covers Q3.',
    '',
    '- البند الأول',
    '- Second item',
    '',
    '> اقتباس من المستند',
    '',
    '| العمود | Value |',
    '| --- | --- |',
    '| أ | 1 |',
    '',
    'Run `gaia init` first.',
    '',
    '```python',
    'print("مرحبا")',
    '```',
].join('\n');

describe('text direction in an answer', () => {
    it('gives every paragraph, heading, list item, quote and table dir="auto"', () => {
        const { container } = render(<RenderedContent content={ANSWER} />);
        const blocks = container.querySelectorAll('p, h3, ul, li, blockquote, table');
        expect(blocks.length).toBeGreaterThan(8);
        for (const el of blocks) expect(el, el.outerHTML).toHaveAttribute('dir', 'auto');
    });

    it('lets a table\'s cells follow the table, since auto skips a cell with its own direction', () => {
        const { container } = render(<RenderedContent content={ANSWER} />);
        for (const cell of container.querySelectorAll('td, th')) expect(cell, cell.outerHTML).not.toHaveAttribute('dir');
    });

    it('keeps inline code and code blocks left to right', () => {
        const { container } = render(<RenderedContent content={ANSWER} />);
        expect(container.querySelector('code.inline-code')).toHaveAttribute('dir', 'ltr');
        expect(container.querySelector('.code-block')).toHaveAttribute('dir', 'ltr');
    });

    it('applies to what the user typed too', () => {
        const message: Message = {
            id: 1, session_id: 's', role: 'user', content: 'ما هي النتائج الرئيسية؟', created_at: '2026-01-01T00:00:00Z',
        } as Message;
        const { container } = render(<MessageBubble message={message} />);
        expect(container.querySelector('.msg-body p')).toHaveAttribute('dir', 'auto');
    });
});

describe('direction characters in what the user typed', () => {
    const userBubble = (content: string) => render(<MessageBubble message={{
        id: 1, session_id: 's', role: 'user', content, created_at: '2026-01-01T00:00:00Z',
    } as Message} />).container.querySelector('.msg-body')!.textContent;

    it('marks an override so the rest of the message is not drawn reversed', () => {
        expect(userBubble('Open report\u202Etxt.md please')).toBe('Open report[U+202E]txt.md please');
    });

    it('marks every embedding and override, and isolates left unpaired', () => {
        expect(markStrayDirection('a\u202Ab\u202Bc\u202Cd\u202De\u202Ef')).toBe('a[U+202A]b[U+202B]c[U+202C]d[U+202D]e[U+202E]f');
        expect(markStrayDirection('x\u2067y')).toBe('x[U+2067]y');
        expect(markStrayDirection('x\u2069y')).toBe('x[U+2069]y');
        expect(markStrayDirection('\u2066a\n\u2069b')).toBe('[U+2066]a\n[U+2069]b');
    });

    it('keeps Arabic, Hebrew, marks, joiners and paired isolates as written', () => {
        const kept = 'ما هي النتائج؟ שלום \u200F\u200E \u200D\u200C 👩\u200D💻 \u2067عربي\u2069 \u2066\u2068x\u2069\u2069';
        expect(markStrayDirection(kept)).toBe(kept);
        expect(userBubble(kept)).toBe(kept);
    });
});
