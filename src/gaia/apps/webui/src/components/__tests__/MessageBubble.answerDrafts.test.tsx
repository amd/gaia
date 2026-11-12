// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * When GAIA re-checks its work the model writes its answer, reads a file back,
 * then writes the answer again. Only one copy may show (#7347), live and when
 * the chat is reopened.
 */

import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MessageBubble } from '../MessageBubble';
import { dropAnswerDrafts } from '../../utils/answerDrafts';
import type { AgentStep, Message } from '../../types';

vi.mock('../../services/api');

const DRAFT = [
    'Done — except the last step, which you declined.',
    '',
    '`summary.md` written in the uxa folder:',
    '',
    '| Month | Total |',
    '|---|---|',
    '| October 2026 | 47.70 |',
    '| November 2026 | 36.75 |',
    '| **Grand total** | **84.45** |',
    '',
    '**Renamed:** `draft_old.txt` → `draft_archive.txt`',
    '',
    '**Not done:** appending `reviewed` to `notes.txt` — you denied the edit, so I stopped.',
].join('\n');

const DRAFT_2 = DRAFT.replace('uxa folder:', 'uxa folder (read back to confirm):');
const FINAL = `${DRAFT_2.replace('so I stopped.', 'so I stopped. Say the word and I\'ll add the line.')}`;

let nextId = 1;
const tool = (name: string): AgentStep => ({
    id: nextId++, type: 'tool', label: name, tool: name, success: true, active: false, timestamp: nextId,
});
const note = (text: string): AgentStep => ({
    id: nextId++, type: 'commentary', label: 'Commentary', detail: text, active: false, timestamp: nextId,
});

const checkedTurn = (): AgentStep[] => [
    note('Reading both expense files first.'),
    tool('read_file'), tool('write_file'),
    note(DRAFT),
    tool('read_file'),
    note(DRAFT_2),
    tool('read_file'),
];

function renderTurn(content: string, steps: AgentStep[], isStreaming = false) {
    const message: Message = {
        id: isStreaming ? -1 : 7, session_id: 's', role: 'assistant', content, created_at: '', rag_sources: null,
    };
    return render(<MessageBubble message={message} agentSteps={steps} isStreaming={isStreaming} agentStepsActive={false} />);
}

const copies = (container: HTMLElement) =>
    (container.textContent ?? '').split('except the last step').length - 1;

describe('one answer per turn', () => {
    it('shows a finished turn\'s answer once, with its narration kept', () => {
        const { container } = renderTurn(FINAL, checkedTurn());
        expect(copies(container)).toBe(1);
        expect(container.textContent).toContain('Reading both expense files first.');
        expect(container.textContent).toContain('Say the word');
    });

    it('drops an earlier draft as soon as a later one restates it', () => {
        const { container } = renderTurn('', checkedTurn(), true);
        expect(copies(container)).toBe(1);
        expect(container.textContent).toContain('read back to confirm');
    });

    it('drops the last draft once most of the restated answer has streamed', () => {
        const partial = FINAL.slice(0, FINAL.indexOf('**Not done:**'));
        const { container } = renderTurn(partial, checkedTurn(), true);
        expect(copies(container)).toBe(1);
    });

    it('keeps the last draft while only the start of the answer has streamed', () => {
        const partial = FINAL.slice(0, FINAL.indexOf('`summary.md`'));
        const { container } = renderTurn(partial, checkedTurn(), true);
        expect(copies(container)).toBe(2);
    });

    it('keeps a note with several findings when the answer repeats only one of them', () => {
        const steps = [
            note('Three problems in the logs. The service failed to start because port 8080 was already in use by another process. '
                + 'The config still sets the deprecated key cache_dir, which this version ignores. '
                + 'The data disk is at 94 percent, so the next large import will fail.'),
            tool('read_file'),
        ];
        const answer = 'The service failed to start because port 8080 was already in use by another process.';
        expect(dropAnswerDrafts(steps, answer)).toEqual(steps);
    });

    it('keeps notes that only narrate the work', () => {
        const steps = [note('I will read the two expense files, then total them per month.'), tool('read_file')];
        expect(dropAnswerDrafts(steps, FINAL)).toEqual(steps);
    });

    it('keeps a long note the answer does not repeat', () => {
        const steps = [
            note('The first file has a header row and twelve lines of expenses, so I will skip the header and add the amounts column by month.'),
            tool('read_file'),
        ];
        expect(dropAnswerDrafts(steps, FINAL)).toEqual(steps);
    });
});
