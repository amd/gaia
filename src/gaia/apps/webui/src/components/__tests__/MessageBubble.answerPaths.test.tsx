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
