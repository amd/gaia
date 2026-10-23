// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A Windows path in an answer must render with every backslash, even where
 * the next character is punctuation that markdown would treat as escaped.
 */

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';

const baseMessage: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'assistant',
    content: '',
    created_at: '2026-10-23T00:00:00.000Z',
    rag_sources: null,
};

function textOf(content: string): string {
    const { container } = render(<MessageBubble message={{ ...baseMessage, content }} />);
    return container.querySelector('.md-content')?.textContent ?? '';
}

describe('Windows paths in prose keep their backslashes', () => {
    it('keeps the backslash before a dot-folder', () => {
        expect(textOf('The details are in this log folder: C:\\Users\\me\\.gaia\\logs')).toBe(
            'The details are in this log folder: C:\\Users\\me\\.gaia\\logs',
        );
    });

    it.each([
        'D:\\work\\_drafts\\#1 notes.txt',
        '\\\\nas\\share\\.cache',
        'C:\\a\\(old)\\-x\\!y',
    ])('keeps %s', (path) => {
        expect(textOf(`Saved to ${path} just now.`)).toBe(`Saved to ${path} just now.`);
    });

    it('keeps paths inside bold and list items', () => {
        expect(textOf('**C:\\Users\\me\\.gaia** is the home')).toBe('C:\\Users\\me\\.gaia is the home');
        expect(textOf('- C:\\x\\.cache\n- \\\\nas\\share\\_tmp')).toContain('C:\\x\\.cache');
        expect(textOf('- C:\\x\\.cache\n- \\\\nas\\share\\_tmp')).toContain('\\\\nas\\share\\_tmp');
    });

    it('ends a path at a closing paren that belongs to the prose', () => {
        expect(textOf('Logs (see C:\\me\\.gaia\\logs) are kept')).toBe('Logs (see C:\\me\\.gaia\\logs) are kept');
    });

    it('still honours a deliberate escape outside a path', () => {
        const { container } = render(
            <MessageBubble message={{ ...baseMessage, content: '\\*literal asterisks\\* near C:\\me\\.gaia' }} />,
        );
        expect(container.querySelector('em')).toBeNull();
        expect(container.querySelector('.md-content')?.textContent).toBe(
            '*literal asterisks* near C:\\me\\.gaia',
        );
    });

    it('leaves inline code and code blocks unchanged', () => {
        expect(textOf('Run `C:\\\\Users\\.gaia` here')).toBe('Run C:\\\\Users\\.gaia here');
        expect(textOf('Use `echo \\.x \\\\nas\\.y` here')).toBe('Use echo \\.x \\\\nas\\.y here');

        const { container } = render(
            <MessageBubble message={{ ...baseMessage, content: '```\nC:\\Users\\me\\.gaia\n```' }} />,
        );
        expect(container.querySelector('pre code')?.textContent).toBe('C:\\Users\\me\\.gaia');
    });

    it('renders every partial chunk of a streaming answer without losing a backslash', () => {
        const full = 'Logs: C:\\Users\\me\\.gaia\\logs and `C:\\x\\.y` and \\\\nas\\s\\.c done';
        const { container, rerender } = render(
            <MessageBubble message={{ ...baseMessage, content: '' }} isStreaming />,
        );
        for (let i = 1; i <= full.length; i++) {
            rerender(<MessageBubble message={{ ...baseMessage, content: full.slice(0, i) }} isStreaming />);
        }
        rerender(<MessageBubble message={{ ...baseMessage, content: full }} />);
        expect(container.querySelector('.md-content')?.textContent).toBe(
            'Logs: C:\\Users\\me\\.gaia\\logs and C:\\x\\.y and \\\\nas\\s\\.c done',
        );
    });
});
