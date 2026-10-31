// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Each block of an answer picks its own direction, so a mixed Arabic/English
// answer reads right to left where it is Arabic; code always reads left to right.

import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { MessageBubble, RenderedContent } from '../MessageBubble';
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
    it('gives every paragraph, heading, list item, quote and cell dir="auto"', () => {
        const { container } = render(<RenderedContent content={ANSWER} />);
        const blocks = container.querySelectorAll('p, h3, ul, li, blockquote, table, td, th');
        expect(blocks.length).toBeGreaterThan(10);
        for (const el of blocks) expect(el, el.outerHTML).toHaveAttribute('dir', 'auto');
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
