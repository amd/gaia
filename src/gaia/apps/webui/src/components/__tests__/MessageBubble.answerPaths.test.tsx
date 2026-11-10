// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A deliverable the answer names ("Saved to `C:\...\answer.md`") opens like a
 * path in the user's own message. Code samples and non-paths stay plain code.
 */

import { fireEvent, render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');
const mockedApi = vi.mocked(api);

const ANSWER_PATH = 'C:\\Users\\me\\gaia-runs\\report\\answer.md';

function renderAnswer(content: string) {
    const message: Message = {
        id: 1, session_id: 's', role: 'assistant', content, created_at: '', rag_sources: null,
    };
    return render(<MessageBubble message={message} />);
}

const links = () => Array.from(document.querySelectorAll('.file-path-link'));

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.openFileOrFolder.mockResolvedValue({ status: 'ok', path: '' });
});

describe('answer file paths', () => {
    it('makes a path in inline code an openable link', () => {
        renderAnswer(`Saved with both citations to \`${ANSWER_PATH}\` — the file reads back as:`);

        expect(links()).toHaveLength(1);
        expect(links()[0].textContent).toBe(ANSWER_PATH);
        expect(document.querySelector('code.inline-code')).toBeNull();

        fireEvent.click(links()[0]);
        expect(mockedApi.openFileOrFolder).toHaveBeenCalledWith(ANSWER_PATH);
    });

    it('accepts forward slashes and a trailing folder separator', () => {
        renderAnswer('Invoices are in `C:/work/expenses/invoices/`.');
        expect(links().map((l) => l.textContent)).toEqual(['C:/work/expenses/invoices/']);
    });

    it('leaves a path inside a fenced code block alone', () => {
        const { container } = renderAnswer(`Run this:\n\n\`\`\`\ntype ${ANSWER_PATH}\n\`\`\`\n\n\`\`\`\n${ANSWER_PATH}\n\`\`\``);

        expect(links()).toHaveLength(0);
        const blocks = container.querySelectorAll('.code-block pre code');
        expect(blocks).toHaveLength(2);
        expect(blocks[1].textContent).toBe(ANSWER_PATH);
    });

    it.each([
        ['a relative folder', 'invoices/'],
        ['a bare file name', 'billing_exceptions.csv'],
        ['a command that contains a path', `type ${ANSWER_PATH}`],
        ['a path followed by a flag', `${ANSWER_PATH} --force`],
        ['a drive letter alone', 'C:'],
        ['a URL', 'https://example.com/a/b.md'],
        ['a POSIX-looking API route', '/api/chat/send'],
    ])('keeps %s in inline code as code', (_label, code) => {
        renderAnswer(`See \`${code}\` here.`);

        expect(links()).toHaveLength(0);
        expect(document.querySelector('code.inline-code')?.textContent).toBe(code);
    });
});

describe('paths with spaces', () => {
    const SPACED = 'C:\\Users\\test\\Documents\\Q3 report.txt';

    it.each([
        ['in prose', `Summarize ${SPACED} for me.`],
        ['in inline code', `Summarize \`${SPACED}\` for me.`],
    ])('links the whole path %s', (_label, content) => {
        renderAnswer(content);
        expect(links().map((l) => l.textContent)).toEqual([SPACED]);
    });

    it('links the whole path in the user\'s own message', () => {
        const message: Message = {
            id: 2, session_id: 's', role: 'user', content: `Summarize ${SPACED}`, created_at: '', rag_sources: null,
        };
        render(<MessageBubble message={message} />);
        expect(links().map((l) => l.textContent)).toEqual([SPACED]);
    });

    it.each([
        'C:\\Users\\Kalin\\OneDrive - AMD\\_drafts\\plan.md',
        'C:\\My Old Docs\\_drafts\\plan.md',
        'C:\\Program Files (x86)\\Common Files\\GAIA\\gaia.exe',
        'C:\\My Old Docs\\_drafts\\my plan.md',
    ])('links %s as one path, in the answer and the user\'s message', (path) => {
        renderAnswer(`Saved to ${path} just now.`);
        expect(links().map((l) => l.textContent)).toEqual([path]);
        document.body.innerHTML = '';
        const message: Message = {
            id: 2, session_id: 's', role: 'user', content: `Summarize ${path}`, created_at: '', rag_sources: null,
        };
        render(<MessageBubble message={message} />);
        expect(links().map((l) => l.textContent)).toEqual([path]);
        fireEvent.click(links()[0]);
        expect(mockedApi.openFileOrFolder).toHaveBeenCalledWith(path);
    });

    it('links a path with several spaces inside Arabic prose', () => {
        const path = 'C:\\Users\\Kalin\\OneDrive - AMD\\_drafts\\plan.md';
        renderAnswer(`احفظ الملف في ${path} ثم أغلق.`);
        expect(links().map((l) => l.textContent)).toEqual([path]);
    });

    it.each([
        ['a folder another separator follows', 'Installed to C:\\Program Files\\GAIA\\gaia.exe today.', 'C:\\Program Files\\GAIA\\gaia.exe'],
        ['a trailing folder separator', 'Look in C:\\Program Files\\GAIA\\ first.', 'C:\\Program Files\\GAIA\\'],
        ['a sentence after a folder', 'Saved to C:\\out\\Q3 and then closed.', 'C:\\out\\Q3'],
        ['a sentence after a file', 'Saved C:\\out\\a.txt and b.txt here.', 'C:\\out\\a.txt'],
        ['a second path', 'Copied C:\\out\\x and D:\\y\\z.txt', 'C:\\out\\x'],
        ['a 32-bit program folder', 'Installed to C:\\Program Files (x86)\\GAIA\\gaia.exe today.', 'C:\\Program Files (x86)\\GAIA\\gaia.exe'],
        ['a three-word file name', 'Open C:\\out\\Q3 sales report.xlsx now', 'C:\\out\\Q3 sales report.xlsx'],
        ['a folder then a sentence naming a file', 'Open C:\\Users\\me\\Documents and look at report.pdf', 'C:\\Users\\me\\Documents'],
        ['a folder then a longer sentence', 'I saved it to C:\\Users\\me\\Downloads so you can open notes.txt', 'C:\\Users\\me\\Downloads'],
        ['a forward slash after a space', 'Files in C:\\Users\\me and/or D:\\x', 'C:\\Users\\me'],
        ['Arabic prose after a folder', 'احفظه في C:\\out\\Q3 ثم أغلق.', 'C:\\out\\Q3'],
        ['an Arabic file name', 'افتح C:\\out\\Q3 تقرير.txt الآن', 'C:\\out\\Q3 تقرير.txt'],
    ])('stops at the right place after %s', (_label, content, first) => {
        renderAnswer(content);
        expect(links()[0].textContent).toBe(first);
    });
});
