// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A Windows path in an answer must render with every backslash, even where
 * the next character is punctuation that markdown would treat as escaped.
 */

import { afterEach, describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';
import { useChatStore } from '../../stores/chatStore';

const baseMessage: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'assistant',
    content: '',
    created_at: '2026-10-23T00:00:00.000Z',
    rag_sources: null,
};

function textOf(content: string, role: Message['role'] = 'assistant'): string {
    const { container } = render(<MessageBubble message={{ ...baseMessage, role, content }} />);
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

    it.each([
        'C:\\My Docs\\_drafts\\plan.md',
        'C:\\Program Files\\.gaia',
        'C:\\Program Files (x86)\\.gaia\\x',
        '\\\\nas\\team share\\_tmp',
    ])(
        'keeps the spaced path %s',
        (path) => {
            expect(textOf(`Saved to ${path} just now.`)).toBe(`Saved to ${path} just now.`);
        },
    );

    it.each([
        'C:\\Users\\Kalin\\OneDrive - AMD\\_drafts\\plan.md',
        'C:\\My Old Docs\\_drafts\\plan.md',
        'C:\\Program Files (x86)\\Common Files\\.cache\\x',
        '\\\\nas\\team share - Q3 (old)\\_tmp\\a.txt',
        'C:\\My Old Docs\\_drafts\\my plan v2.md',
    ])('keeps the path %s whose folders have several spaces', (path) => {
        expect(textOf(`Saved to ${path} just now.`)).toBe(`Saved to ${path} just now.`);
    });

    it.each([
        'C:\\Music\\Smith, John\\_old\\a.mp3',
        'C:\\Music\\Artist; Live\\_x',
        'C:\\a b\\c, d\\_e',
        'C:\\Users\\Kalin\\OneDrive - AMD\\Smith, John\\_old\\a.mp3',
    ])('keeps the path %s whose folder has a comma or semicolon', (path) => {
        expect(textOf(`Saved to ${path} just now.`)).toBe(`Saved to ${path} just now.`);
    });

    it('ends a path at a comma when several words follow it', () => {
        expect(textOf('Saved to C:\\foo, then use snake\\_case')).toBe('Saved to C:\\foo, then use snake_case');
    });

    it('keeps a path with several spaces in the user\'s own message', () => {
        const path = 'C:\\gtest\\docs8\\OneDrive - AMD\\_drafts\\plan.md';
        expect(textOf(`Summarize ${path}`, 'user')).toBe(`Summarize ${path}`);
    });

    it('keeps a path with several spaces inside Arabic prose', () => {
        const path = 'C:\\Users\\Kalin\\OneDrive - AMD\\_drafts\\plan.md';
        expect(textOf(`احفظ الملف في ${path} ثم أغلق.`)).toBe(`احفظ الملف في ${path} ثم أغلق.`);
    });

    it.each([
        ['words with no backslash after them', 'See C:\\foo and then bar \\_x', 'See C:\\foo and then bar _x'],
        ['a comma before the next words', 'Saved to C:\\foo, then use snake\\_case', 'Saved to C:\\foo, then use snake_case'],
        ['a second drive path', 'Copy C:\\a to D:\\b\\_c', 'Copy C:\\a to D:\\b\\_c'],
    ])('ends a spaced path before %s', (_label, content, shown) => {
        expect(textOf(content)).toBe(shown);
    });

    it.each([
        ['folders with spaces', `C:\\${'a b - c\\'.repeat(2000)}_x`],
        ['words with no backslash', `C:\\${'OneDrive - AMD '.repeat(2000)}\\_x`],
        ['unclosed brackets', `C:\\${'a (b '.repeat(2000)}\\_x`],
        ['repeated drive letters', `${'C:\\a b '.repeat(2000)}\\_x`],
    ])('handles 2,000 %s quickly', (_label, content) => {
        const started = performance.now();
        textOf(content);
        expect(performance.now() - started).toBeLessThan(2000);
    });

    it('ends a path at a space when the next word has no backslash', () => {
        expect(textOf('See C:\\me.gaia and \\_literal')).toBe('See C:\\me.gaia and _literal');
    });

    it('ends a path at a space before an escape, so the prose after it still unescapes', () => {
        expect(textOf('Saved to C:\\foo \\*not bold\\* or \\_x')).toBe('Saved to C:\\foo *not bold* or _x');
    });

    it('ends a path at a space before a bracketed aside', () => {
        expect(textOf('Saved to C:\\foo (see docs) and \\_x')).toBe('Saved to C:\\foo (see docs) and _x');
    });

    it('keeps paths inside bold and list items', () => {
        expect(textOf('**C:\\Users\\me\\.gaia** is the home')).toBe('C:\\Users\\me\\.gaia is the home');
        expect(textOf('- C:\\x\\.cache\n- \\\\nas\\share\\_tmp')).toContain('C:\\x\\.cache');
        expect(textOf('- C:\\x\\.cache\n- \\\\nas\\share\\_tmp')).toContain('\\\\nas\\share\\_tmp');
    });

    it('ends a path at a closing paren that belongs to the prose', () => {
        expect(textOf('Logs (see C:\\me\\.gaia\\logs) are kept')).toBe('Logs (see C:\\me\\.gaia\\logs) are kept');
    });

    it('restores each path in place when an unescaped one comes first', () => {
        expect(textOf('C:\\me.gaia and C:\\me\\.gaia')).toBe('C:\\me.gaia and C:\\me\\.gaia');
    });

    it.each(['C:\\logs\\*.txt', 'C:\\logs\\?.txt', '\\\\nas\\share\\*.log'])('keeps the glob %s', (glob) => {
        expect(textOf(`Matched ${glob} today.`)).toBe(`Matched ${glob} today.`);
    });

    it('keeps a glob inside bold', () => {
        const { container } = render(<MessageBubble message={{ ...baseMessage, content: '**C:\\logs\\*.txt** found' }} />);
        expect(container.querySelector('strong')?.textContent).toBe('C:\\logs\\*.txt');
    });

    it('keeps paths on separate lines', () => {
        const { container } = render(<MessageBubble message={{ ...baseMessage, content: 'C:\\a\\.x\nC:\\b\\_y' }} />);
        expect(container.querySelector('.md-content br')).not.toBeNull();
        expect(container.querySelector('.md-content')?.textContent).toBe('C:\\a\\.x\nC:\\b\\_y');
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

describe('a Windows path is one chip with its exact text', () => {
    const PATH = 'C:\\gtest\\run-rc11\\fx\\Team Docs  -  AMD Shared\\budget.txt';

    function chips(content: string): string[] {
        const { container } = render(<MessageBubble message={{ ...baseMessage, role: 'user', content }} />);
        return [...container.querySelectorAll('.file-path-link, .citation-link')].map((el) => el.textContent ?? '');
    }

    afterEach(() => useChatStore.setState({ documents: [] }));

    it('keeps a quoted path with " - " and double spaces whole', () => {
        const content = `Please index "${PATH}" into my document library`;
        expect(chips(content)).toEqual([PATH]);
        expect(textOf(content, 'user')).toBe(content);
    });

    it('keeps the path whole once its file is in the library', () => {
        useChatStore.setState({ documents: [{
            id: 'b', filename: 'budget.txt', filepath: PATH, file_size: 1, chunk_count: 1,
            indexed_at: '2026-01-01T00:00:00Z', last_accessed_at: null, sessions_using: 0, indexing_status: 'complete',
        }] });
        expect(chips(`Please index "${PATH}" into my document library`)).toEqual([PATH]);
        expect(chips(`Please index ${PATH} now`)).toEqual([PATH]);
    });

    it.each([
        ['unquoted', `Please index ${PATH} into my document library`, [PATH]],
        ['in curly quotes', `Please index \u201c${PATH}\u201d now`, [PATH]],
        ['quoted, ending in words with no backslash', 'Open "C:\\My Docs\\notes and more" now', ['C:\\My Docs\\notes and more']],
        ['quoted, with an escapable folder', 'Open "C:\\Team  Docs\\_drafts\\a.md" now', ['C:\\Team  Docs\\_drafts\\a.md']],
        ['quoted, ending in a dot', 'Open "C:\\a\\b." now', ['C:\\a\\b.']],
        ['unquoted, double-spaced last name', 'Open C:\\Team  Docs\\Q3  report.txt now', ['C:\\Team  Docs\\Q3  report.txt']],
        ['unquoted, prose after it', 'See C:\\out\\docs and look at a.pdf', ['C:\\out\\docs']],
        ['quoted, two paths', 'Copy "C:\\a  b\\x.txt" to "D:\\c  d\\y.txt"', ['C:\\a  b\\x.txt', 'D:\\c  d\\y.txt']],
        ['in a quoted sentence', 'He said "see C:\\out\\docs and look" twice', ['C:\\out\\docs']],
    ])('%s', (_label, content, expected) => {
        expect(chips(content)).toEqual(expected);
        expect(textOf(content, 'user')).toBe(content);
    });

    it('shows the label with its spaces kept', () => {
        const { container } = render(<MessageBubble message={{ ...baseMessage, role: 'user', content: `"${PATH}"` }} />);
        expect(getComputedStyle(container.querySelector('.file-path-link')!).whiteSpace).toBe('pre-wrap');
    });

    it.each([
        ['quoted folders with double spaces', `"C:\\${'a  b  -  c\\'.repeat(2000)}_x"`],
        ['unclosed quotes', `${'"C:\\a  b '.repeat(2000)}\\_x`],
        ['double-spaced words with no backslash', `C:\\${'OneDrive  -  AMD  '.repeat(2000)}\\_x`],
        ['double-spaced folders', `C:\\${'a  b  -  c\\'.repeat(2000)}_x`],
    ])('handles 2,000 %s quickly', (_label, content) => {
        const started = performance.now();
        textOf(content, 'user');
        expect(performance.now() - started).toBeLessThan(2000);
    });
});
