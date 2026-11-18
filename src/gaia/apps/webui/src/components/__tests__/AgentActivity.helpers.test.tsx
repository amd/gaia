// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A delegate_task row: progress while helpers run, then how each one went. */

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import { toolResultUpdates } from '../../utils/toolEvents';
import { fromApiMessages } from '../../utils/chatStream';
import { localizeLiveStatus } from '../../utils/liveStatus';
import { helperRunSummary, parseHelperRun } from '../../utils/helperRun';
import { stripIsolates } from '../../i18n/registry';
import { tEn, translator } from '../../i18n/useT';
import { useChatStore } from '../../stores/chatStore';
import type { AgentStep, StreamEvent } from '../../types';

vi.mock('../../services/api');

const ADVICE = 'A FAILED helper returned nothing usable: do that work yourself or tell the user it is missing.';

const helper = (id: number, status: string, over: Record<string, unknown> = {}) => ({
    id, task: `Read notes_0${id}.md and list each decision`, status, conclusion: `Decision ${id}: keep the Friday review.`,
    steps: 2, tool_calls: 1, input_tokens: 900, cached_tokens: 0, output_tokens: 80, wall_s: 3.1, ...over,
});

/** The tool's own result, as delegate_tools.py returns it. */
const fullResult = (helpers: unknown[], status: string, summary: string, over: Record<string, unknown> = {}) => ({
    status, summary, helpers, width: 1, width_source: 'local_serial',
    usage: { input_tokens: 3600, cached_tokens: 0, output_tokens: 320 }, ...over,
});

const ALL_OK = fullResult([1, 2, 3, 4].map((i) => helper(i, 'ok')), 'success', '4 of 4 helpers finished.');
const MIXED = fullResult(
    [
        helper(1, 'ok'),
        helper(2, 'partial', { reason: 'step_limit', conclusion: 'Found two of the three decisions before running out of steps.' }),
        helper(3, 'failed', { reason: 'timeout', conclusion: undefined, error: 'No answer within 600 s.' }),
        helper(4, 'ok'),
    ],
    'partial',
    '2 of 4 helpers finished, 1 stopped short, 1 failed.',
    { advice: ADVICE, width_note: 'Ran one helper at a time: local requests to one model are served one at a time in this process.' },
);
const ALL_FAILED = fullResult(
    [1, 2].map((i) => helper(i, 'failed', { reason: 'error', conclusion: undefined, error: 'ConnectionError: refused' })),
    'error',
    '0 of 2 helpers finished, 2 failed.',
    { advice: ADVICE, error: `0 of 2 helpers finished, 2 failed. ${ADVICE}` },
);

/** A finished delegate_task step with `helpers` filled the way a relayed C++ result fills it. */
function finished(result: Record<string, unknown>, over: Partial<AgentStep> = {}): AgentStep {
    const updates = toolResultUpdates({
        type: 'tool_result', tool: 'delegate_task', summary: String(result.summary),
        success: result.status !== 'error', data: result,
    } as unknown as StreamEvent);
    return {
        id: 1, type: 'tool', label: 'Using tool', tool: 'delegate_task', narration: 'Handing 4 tasks to helpers',
        timestamp: 1, ...updates, latencyMs: 9400, ...over,
    };
}

function open(container: HTMLElement) {
    fireEvent.click(container.querySelector('.flow-tool-header')!);
}

afterEach(() => act(() => useChatStore.getState().setLocale('en')));

describe('while helpers run', () => {
    const running: AgentStep = {
        id: 1, type: 'tool', label: 'Using tool', tool: 'delegate_task', narration: 'Handing 4 tasks to helpers',
        active: true, timestamp: Date.now(),
    };

    it('says it is handing work to helpers before any helper ends', () => {
        const { container } = render(<AgentActivity steps={[running]} isActive running />);
        expect(container.querySelector('.flow-tool.active .flow-tool-label')?.textContent).toBe('Handing work to helpers');
        expect(container.querySelector('.flow-tool-spinner')).not.toBeNull();
    });

    it('shows the progress line on its row once a helper ends', () => {
        const { container } = render(<AgentActivity steps={[running]} isActive running toolStatus="2 of 4 helpers done" />);
        expect(container.querySelector('.flow-tool.active .flow-tool-label')?.textContent).toBe('2 of 4 helpers done');
        // The elapsed time stays on the right.
        expect(container.querySelector('.flow-tool-latency')?.textContent).toMatch(/^\d+s$/);
    });

    it('counts the failures in the progress line', () => {
        const { container } = render(<AgentActivity steps={[running]} isActive running toolStatus="3 of 4 helpers done, 1 failed" />);
        expect(container.querySelector('.flow-tool-label')?.textContent).toBe('3 of 4 helpers done, 1 failed');
    });

    it('says the progress in Arabic with the numbers kept whole', () => {
        const ar = translator('ar');
        expect(stripIsolates(localizeLiveStatus(ar, '3 of 4 helpers done, 1 failed')))
            .toBe('أنهى المساعدون: 3 من 4، فشل منهم: 1 (3 of 4 helpers done, 1 failed)');
        expect(stripIsolates(localizeLiveStatus(ar, '1 of 1 helper done'))).toBe('أنهى المساعدون: 1 من 1 (1 of 1 helper done)');
        expect(localizeLiveStatus(tEn, '1 of 1 helper done')).toBe('1 of 1 helper done');
    });
});

describe('a finished delegate_task row', () => {
    it('reads as done, with the summary beside the label', () => {
        const { container } = render(<AgentActivity steps={[finished(ALL_OK)]} isActive={false} />);
        const row = container.querySelector('.flow-tool')!;
        expect(row.querySelector('.flow-tool-label')?.textContent).toBe('Handed work to helpers');
        expect(row.querySelector('.flow-tool-outcome')?.textContent).toBe('4 of 4 helpers finished.');
        expect(row.className).not.toMatch(/\b(error|partial)\b/);
        expect(row.querySelector('.lucide-users')).not.toBeNull();
    });

    it('shows a partial run as a warning, not a failure', () => {
        const { container } = render(<AgentActivity steps={[finished(MIXED)]} isActive={false} />);
        const row = container.querySelector('.flow-tool')!;
        expect(row.className).toMatch(/\bpartial\b/);
        expect(row.className).not.toMatch(/\berror\b/);
        expect(row.querySelector('.flow-tool-failure')).toBeNull();
        expect(row.querySelector('.flow-tool-outcome.warn')?.textContent).toBe('2 of 4 helpers finished, 1 stopped short, 1 failed.');
        expect(row.querySelector('.flow-tool-warn-icon')).not.toBeNull();
    });

    it('shows a run where every helper failed as failed, with the summary and never the advice', () => {
        const { container } = render(<AgentActivity steps={[finished(ALL_FAILED)]} isActive={false} />);
        const row = container.querySelector('.flow-tool')!;
        expect(row.className).toMatch(/\berror\b/);
        expect(row.querySelector('.flow-tool-label')?.textContent).toBe('Handing work to helpers failed');
        expect(row.querySelector('.flow-tool-failure')?.textContent).toBe('0 of 2 helpers finished, 2 failed.');
        open(container);
        expect(container.textContent).not.toContain('nothing usable');
        expect(container.textContent).not.toContain('ConnectionError');
    });
});

describe('the opened row', () => {
    it('lists each helper in order, with its task, outcome, steps and time', () => {
        const { container } = render(<AgentActivity steps={[finished(MIXED)]} isActive={false} />);
        open(container);
        const items = within(screen.getByRole('list', { name: 'Helpers' })).getAllByRole('listitem');
        expect(items).toHaveLength(4);
        expect(items.map((li) => li.querySelector('.helper-task')?.textContent)).toEqual([1, 2, 3, 4].map((i) => `Read notes_0${i}.md and list each decision`));

        expect(items[0].getAttribute('aria-label')).toBe('Read notes_01.md and list each decision, Finished, Decision 1: keep the Friday review.');
        expect(items[1].querySelector('.helper-outcome.partial')?.textContent).toBe('Stopped short (out of steps)');
        expect(items[1].textContent).toContain('Found two of the three decisions');
        expect(items[2].querySelector('.helper-outcome.failed')?.textContent).toBe('Failed: no answer in time');
        expect(items[0].querySelector('.helper-meta')?.textContent).toBe('2 steps · 3.1s');
        // Each number, and the time with its unit, keeps left to right in an rtl row.
        expect([...items[0].querySelectorAll('.helper-meta bdi[dir="ltr"]')].map((b) => b.textContent)).toEqual(['2', '3.1s']);

        expect(screen.getAllByRole('img', { name: 'Finished' })).toHaveLength(2);
        expect(screen.getByRole('img', { name: 'Stopped short' })).toBeInTheDocument();
        expect(screen.getByRole('img', { name: 'Failed' })).toBeInTheDocument();
    });

    it('rewords the width note for a person and shows no arguments, raw result or advice', () => {
        const { container } = render(<AgentActivity steps={[finished(MIXED, { detail: 'tasks: [{"task": "Read notes_01.md"}]' })]} isActive={false} />);
        open(container);
        expect(container.querySelector('.helpers-summary')?.textContent).toBe('2 of 4 helpers finished, 1 stopped short, 1 failed.');
        expect(container.querySelector('.helpers-width-note')?.textContent).toBe('Helpers ran one at a time on this model.');
        expect(container.textContent).not.toContain('local requests to one model');
        expect(container.textContent).not.toContain('Arguments');
        expect(container.querySelector('.step-detail-result')).toBeNull();
        expect(container.textContent).not.toContain(ADVICE);
        expect(container.textContent).not.toContain('input_tokens');
    });

    it.each([
        ['step_limit', 'partial', 'Stopped short (out of steps)'],
        ['error', 'partial', 'Stopped short (the model server failed)'],
        ['loop', 'failed', 'Failed: kept repeating a step'],
        ['empty', 'failed', 'Failed: no answer'],
        ['error', 'failed', 'Failed: the model server failed'],
        ['timeout', 'failed', 'Failed: no answer in time'],
        ['cancelled', 'failed', 'Failed: stopped'],
        ['quota', 'failed', 'Failed: quota'],
    ])('says reason %s in plain words (%s)', (reason, status, said) => {
        const run = fullResult([helper(1, status, { reason, conclusion: undefined })], 'partial', '0 of 1 helper finished.');
        const { container } = render(<AgentActivity steps={[finished(run)]} isActive={false} />);
        open(container);
        expect(container.querySelector('.helper-outcome')?.textContent).toBe(said);
    });

    it('shows a finished helper with no conclusion by its task alone', () => {
        const run = fullResult([helper(1, 'ok', { conclusion: undefined })], 'success', '1 of 1 helper finished.');
        const { container } = render(<AgentActivity steps={[finished(run)]} isActive={false} />);
        open(container);
        expect(container.querySelector('.helper-note')).toBeNull();
        expect(screen.getByRole('listitem').getAttribute('aria-label')).toBe('Read notes_01.md and list each decision, Finished');
    });

    it('keeps a conclusion as plain text, never markup', () => {
        const run = fullResult([helper(1, 'ok', { conclusion: '<img src=x onerror=alert(1)> **bold**' })], 'success', '1 of 1 helper finished.');
        const { container } = render(<AgentActivity steps={[finished(run)]} isActive={false} />);
        open(container);
        expect(container.querySelector('.helper-note img')).toBeNull();
        expect(container.querySelector('.helper-note strong')).toBeNull();
        expect(container.querySelector('.helper-note')?.textContent).toBe('<img src=x onerror=alert(1)> **bold**');
    });

    it('offers Show more for a conclusion past two lines, and Show less after', () => {
        const heights = vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(function (this: HTMLElement) {
            return this.classList.contains('helper-note') ? 120 : 0;
        });
        try {
            const long = fullResult([helper(1, 'ok', { conclusion: 'A long answer. '.repeat(60) })], 'success', '1 of 1 helper finished.');
            const { container } = render(<AgentActivity steps={[finished(long)]} isActive={false} />);
            open(container);
            const more = screen.getByRole('button', { name: 'Show more' });
            expect(more.getAttribute('aria-expanded')).toBe('false');
            expect(more.getAttribute('aria-controls')).toBe(container.querySelector('.helper-note')!.id);
            fireEvent.click(more);
            expect(container.querySelector('.helper-note.is-open')).not.toBeNull();
            fireEvent.click(screen.getByRole('button', { name: 'Show less' }));
            expect(container.querySelector('.helper-note.is-open')).toBeNull();
        } finally {
            heights.mockRestore();
        }
    });

    it('offers no toggle for a conclusion that fits', () => {
        const { container } = render(<AgentActivity steps={[finished(ALL_OK)]} isActive={false} />);
        open(container);
        expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull();
    });

    it('shows at most 16 helpers and says how many more there are', () => {
        const many = fullResult(Array.from({ length: 19 }, (_, i) => helper(i + 1, 'ok')), 'success', '19 of 19 helpers finished.');
        const { container } = render(<AgentActivity steps={[finished(many)]} isActive={false} />);
        open(container);
        expect(screen.getAllByRole('listitem')).toHaveLength(16);
        expect(container.querySelector('.helpers-hidden')?.textContent).toBe('3 more helpers not shown');
        // The list is incomplete, so the backend's own count stands.
        expect(container.querySelector('.helpers-summary')?.textContent).toBe('19 of 19 helpers finished.');
    });

    it('lists 16 helpers in id order whatever order they arrived in', () => {
        const ids = [16, 3, 9, 1, 12, 5, 14, 7, 2, 11, 4, 15, 8, 6, 13, 10];
        const run = fullResult(ids.map((i) => helper(i, 'ok', { task: `Task ${i}` })), 'success', '16 of 16 helpers finished.');
        const { container } = render(<AgentActivity steps={[finished(run)]} isActive={false} />);
        open(container);
        expect(screen.getAllByRole('listitem').map((li) => li.querySelector('.helper-task')?.textContent))
            .toEqual(Array.from({ length: 16 }, (_, i) => `Task ${i + 1}`));
        expect(container.querySelector('.helpers-hidden')).toBeNull();
    });
});

describe('a result the row cannot read', () => {
    it.each([
        ['no helpers list', { status: 'success', summary: '4 of 4 helpers finished.' }],
        ['helpers not a list', { status: 'success', summary: '4 of 4 helpers finished.', helpers: 'four' }],
        ['an unknown status', { status: 'done', summary: '4 of 4 helpers finished.', helpers: [] }],
        ['no summary', { status: 'success', helpers: [helper(1, 'ok')] }],
        ['not an object', 'four helpers'],
    ])('does not crash and shows the plain summary (%s)', (_what, data) => {
        const step = finished({ summary: '4 of 4 helpers finished.', status: 'success' }, { helpers: data, result: '4 of 4 helpers finished.' });
        const { container } = render(<AgentActivity steps={[step]} isActive={false} />);
        expect(container.querySelector('.flow-tool-outcome')?.textContent).toBe('4 of 4 helpers finished.');
        open(container);
        expect(container.querySelector('.helpers-view')).toBeNull();
    });

    it('skips helper entries with the wrong shape and keeps the rest', () => {
        const run = fullResult([helper(1, 'ok'), { id: 'two', status: 'ok' }, null, helper(3, 'maybe'), helper(4, 'ok', { task: 7, steps: 'x', wall_s: null })], 'success', '2 of 4 helpers finished.');
        const parsed = parseHelperRun(run)!;
        expect(parsed.helpers.map((h) => h.id)).toEqual([1, 4]);
        expect(parsed.helpers[1]).toMatchObject({ task: '', steps: undefined, wallS: undefined });
        // Some entries were dropped, so the counts would be wrong: the backend's line stands.
        expect(helperRunSummary(parsed, tEn)).toBe('2 of 4 helpers finished.');
        const { container } = render(<AgentActivity steps={[finished(run)]} isActive={false} />);
        open(container);
        expect(screen.getAllByRole('listitem')[1].querySelector('.helper-task')?.textContent).toBe('Helper 4');
    });
});

describe('where the helpers come from', () => {
    it('reads the in-process event, which carries them in result_data', () => {
        const view = {
            type: 'helpers', status: 'partial', success: true, summary: MIXED.summary,
            helpers: (MIXED.helpers as Record<string, unknown>[]).map(({ id, task, status, reason, conclusion, steps, wall_s }) => ({ id, task, status, reason, conclusion, steps, wall_s })),
            width: 1, width_note: (MIXED as Record<string, unknown>).width_note,
        };
        const updates = toolResultUpdates({ type: 'tool_result', summary: MIXED.summary, success: true, status: 'partial', result_data: view } as unknown as StreamEvent);
        expect(updates.helpers).toBe(view);
        expect(updates.success).toBe(true);
        expect(parseHelperRun(updates.helpers)?.status).toBe('partial');
    });

    it('reads the relayed C++ event, which carries the whole result in data', () => {
        const updates = toolResultUpdates({ type: 'tool_result', tool: 'delegate_task', summary: 'x', success: true, data: MIXED } as unknown as StreamEvent);
        expect(parseHelperRun(updates.helpers)?.helpers).toHaveLength(4);
    });

    it('takes no helpers from another tool', () => {
        const updates = toolResultUpdates({ type: 'tool_result', tool: 'read_file', summary: 'x', success: true, data: MIXED } as unknown as StreamEvent);
        expect(updates.helpers).toBeUndefined();
    });

    it('still lists them in a reopened chat, from the steps the API sends back', () => {
        const saved = {
            id: 1, type: 'tool', label: 'Using tool', tool: 'delegate_task', active: false, success: true,
            result: MIXED.summary, timestamp: 1,
            helpers: {
                type: 'helpers', status: 'partial', success: true, summary: MIXED.summary, hidden: null, width: 1, width_note: null,
                helpers: [{ id: 1, task: 'Read notes_01.md', status: 'ok', reason: null, conclusion: 'Keep it.', steps: 2, wall_s: 3.1 }],
            },
        };
        const [reloaded] = fromApiMessages([{ id: 9, role: 'assistant', content: 'Done.', agent_steps: [saved] }]);
        const { container } = render(<AgentActivity steps={reloaded.agentSteps!} isActive={false} />);
        open(container);
        expect(container.querySelector('.helper-task')?.textContent).toBe('Read notes_01.md');
        expect(container.querySelector('.helpers-width-note')).toBeNull();
    });
});

describe('in Arabic', () => {
    it('words the row and the list in Arabic, with English in brackets on whole lines only', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        const { container } = render(<AgentActivity steps={[finished(MIXED)]} isActive={false} />);
        expect(stripIsolates(container.querySelector('.flow-tool-outcome')!.textContent!))
            .toBe('أنهى المساعدون: 2 من 4، توقف قبل الإتمام: 1، فشل: 1. (2 of 4 helpers finished, 1 stopped short, 1 failed.)');
        open(container);
        expect(stripIsolates(container.querySelectorAll('.helper-outcome')[0].textContent!)).toBe('توقف قبل الإتمام (نفدت خطواته)');
        expect(container.querySelector('.helper-meta')?.textContent).toBe('الخطوات: 2 · 3.1s');
    });
});
