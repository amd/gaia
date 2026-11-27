// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** "Undo this turn" / "Review changes" under an answer (#5225). */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TurnChanges } from '../TurnChanges';
import { ChangesReview } from '../ChangesReview';
import { DiffCard } from '../render/DiffCard';
import { UndoHistorySettings } from '../settings/UndoHistorySettings';
import { resetTurnChangesCache } from '../../hooks/useTurnChanges';
import { stripIsolates } from '../../i18n/registry';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import type { TurnChanges as Turn } from '../../types/changes';
import type { AgentStep } from '../../types';
import { ranChangingTool } from '../../hooks/useSessionProgram';

const turn = (over: Partial<Turn> = {}): Turn => ({
    turn_id: 't1',
    session_id: 's1',
    message_id: 7,
    started_at: '2026-10-16T00:00:00+00:00',
    finished_at: '2026-10-16T00:00:05+00:00',
    restored_at: null,
    files: [
        { path: 'C:\\work\\a.txt', change: 'edited', tool: 'edit_file' },
        { path: 'C:\\work\\b.txt', change: 'created', tool: 'write_file' },
    ],
    untracked: [],
    incomplete: false,
    ...over,
});

beforeEach(() => {
    resetTurnChangesCache();
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe('TurnChanges', () => {
    it('renders nothing for a turn that changed no files', async () => {
        const list = vi.spyOn(api, 'listTurnChanges').mockResolvedValue({ turns: [] });
        const { container } = render(<TurnChanges sessionId="s1" messageId={7} />);
        await waitFor(() => expect(list).toHaveBeenCalled());
        expect(container).toBeEmptyDOMElement();
    });

    it('shows the count and both actions for a turn that changed files', async () => {
        vi.spyOn(api, 'listTurnChanges').mockResolvedValue({ turns: [turn()] });
        render(<TurnChanges sessionId="s1" messageId={7} />);
        expect(await screen.findByText('Changed 2 files')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Review changes/ })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Undo this turn/ })).toBeInTheDocument();
    });

    it('fetches one listing for every message in a chat', async () => {
        const list = vi.spyOn(api, 'listTurnChanges').mockResolvedValue({ turns: [turn()] });
        render(
            <>
                <TurnChanges sessionId="s1" messageId={5} />
                <TurnChanges sessionId="s1" messageId={6} />
                <TurnChanges sessionId="s1" messageId={7} />
            </>,
        );
        expect(await screen.findByText('Changed 2 files')).toBeInTheDocument();
        expect(list).toHaveBeenCalledTimes(1);
    });

    it('undoes after confirming and reports what it restored', async () => {
        vi.spyOn(api, 'listTurnChanges')
            .mockResolvedValueOnce({ turns: [turn()] })
            .mockResolvedValueOnce({ turns: [turn({ restored_at: '2026-10-16T01:00:00+00:00' })] });
        const undo = vi.spyOn(api, 'undoTurn').mockResolvedValue({
            turn_id: 't1', restored: ['C:\\work\\a.txt'], removed: ['C:\\work\\b.txt'],
            kept_folders: [], untracked: [], incomplete: false,
        });
        render(<TurnChanges sessionId="s1" messageId={7} />);
        fireEvent.click(await screen.findByRole('button', { name: /Undo this turn/ }));
        expect(undo).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
        await waitFor(() => expect(undo).toHaveBeenCalledWith('s1', 't1', false));
        expect(await screen.findByRole('status')).toHaveTextContent('restored 1 file, removed 1 new file');
        expect(await screen.findByText('Changed 2 files · undone')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Undo this turn/ })).not.toBeInTheDocument();
    });

    it('on a conflict names the problem and offers to undo anyway', async () => {
        vi.spyOn(api, 'listTurnChanges').mockResolvedValue({ turns: [turn()] });
        const undo = vi.spyOn(api, 'undoTurn')
            .mockRejectedValueOnce(new api.ApiError(
                'x', 409,
                '1 file(s) changed after this turn: C:\\work\\a.txt. Undoing it would overwrite those changes. Nothing was restored.',
                'conflict',
            ))
            .mockResolvedValueOnce({
                turn_id: 't1', restored: ['C:\\work\\a.txt'], removed: [], kept_folders: [], untracked: [], incomplete: false,
            });
        render(<TurnChanges sessionId="s1" messageId={7} />);
        fireEvent.click(await screen.findByRole('button', { name: /Undo this turn/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Nothing was restored');
        fireEvent.click(screen.getByRole('button', { name: /Undo anyway/ }));
        await waitFor(() => expect(undo).toHaveBeenLastCalledWith('s1', 't1', true));
    });

    it('says when undo may be incomplete, and why', async () => {
        vi.spyOn(api, 'listTurnChanges').mockResolvedValue({
            turns: [turn({ incomplete: true, untracked: [{ kind: 'shell', detail: 'npm run build', paths: [] }] })],
        });
        render(<TurnChanges sessionId="s1" messageId={7} />);
        expect(await screen.findByRole('note')).toHaveTextContent(
            "GAIA also ran 1 command. Undo only restores files GAIA edited itself, so anything it changed or installed stays.",
        );
        expect(screen.getByText('Command: npm run build')).toBeInTheDocument();
    });

    it('groups identical snippets into one line', async () => {
        const snippet = { kind: 'run_python', detail: 'reads incident logs, counts errors by hour', paths: [] };
        vi.spyOn(api, 'listTurnChanges').mockResolvedValue({
            turns: [turn({ files: [], incomplete: true, untracked: Array(7).fill(snippet) })],
        });
        render(<TurnChanges sessionId="s1" messageId={7} />);
        expect(await screen.findByRole('note')).toHaveTextContent(
            "GAIA ran 7 Python snippets. Undo only restores files GAIA edited itself, so anything they changed or installed stays.",
        );
        expect(screen.getAllByRole('listitem')).toHaveLength(1);
        expect(screen.getByText('Python: reads incident logs, counts errors by hour ×7')).toBeInTheDocument();
        // Nothing GAIA tracked, so there is nothing to review or undo.
        expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it('lists different snippets under one expandable line', async () => {
        vi.spyOn(api, 'listTurnChanges').mockResolvedValue({
            turns: [turn({
                incomplete: true,
                untracked: [
                    { kind: 'run_python', detail: 'save totals to totals.csv', paths: [] },
                    { kind: 'run_python', detail: 'write the chart', paths: [] },
                    { kind: 'run_python', detail: 'write the chart', paths: [] },
                ],
            })],
        });
        render(<TurnChanges sessionId="s1" messageId={7} />);
        const summary = await screen.findByText('Python snippets ×3');
        expect(summary.tagName).toBe('SUMMARY');
        expect(screen.getByText('save totals to totals.csv')).toBeInTheDocument();
        expect(screen.getByText('write the chart ×2')).toBeInTheDocument();
    });

    it('a turn that only ran read-only work shows nothing', async () => {
        const list = vi.spyOn(api, 'listTurnChanges').mockResolvedValue({
            turns: [turn({ files: [], incomplete: false, untracked: [] })],
        });
        const { container } = render(<TurnChanges sessionId="s1" messageId={7} />);
        await waitFor(() => expect(list).toHaveBeenCalled());
        expect(container).toBeEmptyDOMElement();
    });

    it('a listing error is shown, not swallowed', async () => {
        vi.spyOn(api, 'listTurnChanges').mockRejectedValue(new Error('backend down'));
        render(<TurnChanges sessionId="s1" messageId={7} />);
        expect(await screen.findByRole('note')).toHaveTextContent('backend down');
    });

    it('says GAIA cannot be reached instead of the raw "Failed to fetch", in the chosen language (#6588)', async () => {
        vi.spyOn(api, 'listTurnChanges').mockRejectedValue(new TypeError('Failed to fetch'));
        useChatStore.setState({ locale: 'ar' });
        try {
            render(<TurnChanges sessionId="s1" messageId={7} />);
            const note = await screen.findByRole('note');
            expect(note).toHaveTextContent('تعذّر التحقق من تغييرات الملفات في هذا الدور: تعذّر الوصول إلى GAIA حاليًا.');
            expect(note).not.toHaveTextContent('Failed to fetch');
        } finally {
            useChatStore.setState({ locale: 'en' });
        }
    });

    it('words the row, its confirm and its result in the chosen language (#8828)', async () => {
        vi.spyOn(api, 'listTurnChanges')
            .mockResolvedValueOnce({ turns: [turn()] })
            .mockResolvedValueOnce({ turns: [turn({ restored_at: '2026-10-16T01:00:00+00:00' })] });
        vi.spyOn(api, 'undoTurn').mockResolvedValue({
            turn_id: 't1', restored: ['C:\\work\\a.txt'], removed: ['C:\\work\\b.txt'],
            kept_folders: [], untracked: [], incomplete: false,
        });
        useChatStore.setState({ locale: 'ar' });
        try {
            render(<TurnChanges sessionId="s1" messageId={7} />);
            expect(await screen.findByText(/الملفات المُغيَّرة: 2/)).toBeInTheDocument();
            expect(screen.getByRole('button', { name: /مراجعة التغييرات/ })).toBeInTheDocument();
            fireEvent.click(screen.getByRole('button', { name: /التراجع عن هذا الدور/ }));
            expect(screen.getByRole('group')).toHaveTextContent('إعادة الملفات إلى ما كانت عليه قبل هذا الدور؟');
            fireEvent.click(screen.getByRole('button', { name: /\(Undo\)/ }));
            const status = await screen.findByRole('status');
            expect(stripIsolates(status.textContent ?? '')).toBe(
                'تم التراجع: استُعيد ملف واحد، حُذف ملف جديد واحد. (Undone: restored 1 file, removed 1 new file.)',
            );
            expect(await screen.findByText(/· تم التراجع/)).toBeInTheDocument();
        } finally {
            useChatStore.setState({ locale: 'en' });
        }
    });

    it('a 409 that is not a conflict offers no undo-anyway', async () => {
        vi.spyOn(api, 'listTurnChanges').mockResolvedValue({ turns: [turn()] });
        vi.spyOn(api, 'undoTurn').mockRejectedValue(
            new api.ApiError('x', 409, 'The saved copy of a.txt is damaged; nothing was restored.', null),
        );
        render(<TurnChanges sessionId="s1" messageId={7} />);
        fireEvent.click(await screen.findByRole('button', { name: /Undo this turn/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('damaged');
        expect(screen.queryByRole('button', { name: /Undo anyway/ })).not.toBeInTheDocument();
    });

    it('Review changes opens a diff view fed by the saved copies', async () => {
        vi.spyOn(api, 'listTurnChanges').mockResolvedValue({ turns: [turn()] });
        const diff = vi.spyOn(api, 'getTurnDiff').mockResolvedValue({
            turn_id: 't1',
            files: [
                { path: 'C:\\work\\a.txt', change: 'edited', binary: false, too_large: false, truncated: false,
                    unified: '--- a/a.txt\r\n+++ b/a.txt\r\n@@ -1 +1 @@\r\n-old\r\n+new\r\n', before_size: 4, after_size: 4 },
                { path: 'C:\\work\\img.png', change: 'created', binary: true, too_large: false, truncated: false,
                    unified: '', before_size: 0, after_size: 120 },
                { path: 'C:\\work\\big.log', change: 'edited', binary: false, too_large: true, truncated: false,
                    unified: '', before_size: 9000000, after_size: 9000100 },
            ],
        });
        render(<TurnChanges sessionId="s1" messageId={7} />);
        fireEvent.click(await screen.findByRole('button', { name: /Review changes/ }));
        expect(await screen.findByRole('dialog', { name: 'Review changes' })).toBeInTheDocument();
        await waitFor(() => expect(diff).toHaveBeenCalledWith('s1', 't1'));
        expect(await screen.findByText('-old')).toHaveClass('render-diff__line--removed');
        expect(screen.getByText('+new')).toHaveClass('render-diff__line--added');
        expect(screen.getByText(/Binary file: 0 → 120 bytes/)).toBeInTheDocument();
        expect(screen.getByText(/Too large to show: 9000000 → 9000100 bytes/)).toBeInTheDocument();
        expect(screen.getAllByText('Edited')).toHaveLength(2);
    });

    it('the diff view speaks Arabic', async () => {
        vi.spyOn(api, 'getTurnDiff').mockResolvedValue({
            turn_id: 't1',
            files: [
                { path: 'img.png', change: 'created', binary: true, too_large: false, truncated: false,
                    unified: '', before_size: 0, after_size: 120 },
            ],
        });
        useChatStore.setState({ locale: 'ar' });
        try {
            render(<ChangesReview sessionId="s1" turn={turn({ restored_at: '2026-10-16T00:01:00+00:00' })} onClose={() => {}} />);
            const dialog = await screen.findByRole('dialog', { name: /مراجعة التغييرات/ });
            expect(dialog.querySelector('h3')).toHaveTextContent('التغييرات في هذه الجولة');
            await waitFor(() => expect(dialog).toHaveTextContent('ملف ثنائي: 0 ← 120 بايت.'));
            expect(dialog).toHaveTextContent('منشأ');
            expect(dialog).toHaveTextContent('تم التراجع عن هذه الجولة');
        } finally {
            useChatStore.setState({ locale: 'en' });
        }
    });
});

describe('DiffCard', () => {
    it('drops the CR of CRLF lines and the trailing empty line', () => {
        const { container } = render(<DiffCard data={{ unified: '-a\r\n+b\r\n' }} />);
        const lines = Array.from(container.querySelectorAll('.render-diff__line')).map((el) => el.textContent);
        expect(lines).toEqual(['-a', '+b']);
    });

    it('marks a right-to-left override in the body, the title and the path', () => {
        const unified = '--- /dev/null\n+++ b/report\u202Etxt.md\n@@ -0,0 +1 @@\n+hi \u202Eolleh\n';
        const { container } = render(<DiffCard data={{ title: 'report\u202Etxt.md', path: '/d/report\u202Etxt.md', unified }} />);
        const lines = Array.from(container.querySelectorAll('.render-diff__line')).map((el) => el.textContent);
        expect(lines).toEqual(['+hi [U+202E]olleh']);
        expect(container.querySelector('.render-diff__title')).toHaveTextContent('report[U+202E]txt.md');
        expect(container.querySelector('.render-diff__path')).toHaveTextContent('/d/report[U+202E]txt.md');
        expect(container.textContent).not.toContain('\u202E');
    });

    it('shows only the changed lines of a new file, without the diff headers', () => {
        const { container } = render(<DiffCard data={{
            unified: '--- /dev/null\n+++ b/shopping.txt\n@@ -0,0 +1,3 @@\n+milk\n+eggs\n+bread\n',
        }} />);
        const lines = Array.from(container.querySelectorAll('.render-diff__line')).map((el) => el.textContent);
        expect(lines).toEqual(['+milk', '+eggs', '+bread']);
    });

    it('marks the gap between hunks and colors a removed line that starts with dashes', () => {
        const { container } = render(<DiffCard data={{
            unified: '--- a/x.md\n+++ b/x.md\n@@ -1 +1 @@\n-a\n+b\n@@ -9 +9 @@\n--- rule\n+***\n',
        }} />);
        const lines = Array.from(container.querySelectorAll('.render-diff__line'));
        expect(lines.map((el) => el.textContent)).toEqual(['-a', '+b', '⋯', '--- rule', '+***']);
        expect(lines[2]).toHaveClass('render-diff__line--hunk');
        expect(lines[3]).toHaveClass('render-diff__line--removed');
    });

    it('titles a file diff with its name and shows the full path under it, breaking only between folders', () => {
        const { container } = render(<DiffCard data={{ path: '/home/me/documents/shopping.txt', badge: 'Created', unified: '' }} />);
        expect(container.querySelector('.render-diff__title')).toHaveTextContent(/^Createdshopping\.txt$/);
        const path = container.querySelector('.render-diff__path');
        expect(path).toHaveTextContent('/home/me/documents/shopping.txt');
        expect(path?.querySelectorAll('wbr')).toHaveLength(5);
    });

    it('shows a note and badge, and no body when there is no text diff', () => {
        const { container } = render(<DiffCard data={{ title: 'x.bin', badge: 'Deleted', note: 'Binary file', unified: '' }} />);
        expect(screen.getByText('Deleted')).toBeInTheDocument();
        expect(screen.getByText('Binary file')).toBeInTheDocument();
        expect(container.querySelector('.render-diff__body')).toBeNull();
    });
});

describe('TurnChanges on gaiacpp', () => {
    const capabilities = (program: 'gaiapy' | 'gaiacpp' | null) => vi.spyOn(api, 'getCapabilities')
        .mockResolvedValue({ session_id: 's1', agent_type: 'gaia', tools: [], program });
    const LINE = "Undo isn't available on Fast and light: it doesn't record file changes yet. Full-featured does.";

    it('says undo is not available where Undo would be, for a turn that wrote a file', async () => {
        vi.spyOn(api, 'listTurnChanges').mockResolvedValue({ turns: [] });
        capabilities('gaiacpp');
        render(<TurnChanges sessionId="s1" messageId={7} ranChangingTool />);
        expect(await screen.findByRole('note')).toHaveTextContent(LINE);
        expect(screen.queryByRole('button', { name: /Undo this turn/ })).not.toBeInTheDocument();
    });

    it('says nothing on the Python agent, whose unrecorded turn changed no files', async () => {
        const list = vi.spyOn(api, 'listTurnChanges').mockResolvedValue({ turns: [] });
        const asked = capabilities('gaiapy');
        const { container } = render(<TurnChanges sessionId="s1" messageId={7} ranChangingTool />);
        await waitFor(() => expect(list).toHaveBeenCalled());
        await waitFor(() => expect(asked).toHaveBeenCalled());
        await asked.mock.results[0].value;
        expect(container).toBeEmptyDOMElement();
    });

    it('says nothing, and asks nothing, under a turn that only read or answered', async () => {
        const list = vi.spyOn(api, 'listTurnChanges').mockResolvedValue({ turns: [] });
        const asked = capabilities('gaiacpp');
        const { container } = render(<TurnChanges sessionId="s1" messageId={7} />);
        await waitFor(() => expect(list).toHaveBeenCalled());
        expect(container).toBeEmptyDOMElement();
        expect(asked).not.toHaveBeenCalled();
    });

    it('claims nothing when the chat cannot say which agent runs it', async () => {
        const list = vi.spyOn(api, 'listTurnChanges').mockResolvedValue({ turns: [] });
        const asked = vi.spyOn(api, 'getCapabilities').mockRejectedValue(new Error('The agent is not running'));
        const { container } = render(<TurnChanges sessionId="s1" messageId={7} ranChangingTool />);
        await waitFor(() => expect(list).toHaveBeenCalled());
        await waitFor(() => expect(asked).toHaveBeenCalled());
        await asked.mock.results[0].value.catch(() => undefined);
        expect(container).toBeEmptyDOMElement();
    });

    it('keeps Undo for a turn that has a record', async () => {
        vi.spyOn(api, 'listTurnChanges').mockResolvedValue({ turns: [turn()] });
        const asked = capabilities('gaiacpp');
        render(<TurnChanges sessionId="s1" messageId={7} ranChangingTool />);
        expect(await screen.findByRole('button', { name: /Undo this turn/ })).toBeInTheDocument();
        expect(screen.queryByText(LINE)).not.toBeInTheDocument();
        expect(asked).not.toHaveBeenCalled();
    });

    it('counts only write and shell tools that ran', () => {
        const step = (over: Partial<AgentStep>): AgentStep => ({ id: 1, type: 'tool', label: '', timestamp: 0, ...over } as AgentStep);
        expect(ranChangingTool([step({ tool: 'write_file', success: true })])).toBe(true);
        expect(ranChangingTool([step({ tool: 'edit_file' })])).toBe(true);
        expect(ranChangingTool([step({ tool: 'run_shell_command' })])).toBe(true);
        expect(ranChangingTool([step({ tool: 'read_file' })])).toBe(false);
        expect(ranChangingTool([step({ tool: 'write_file', declined: true })])).toBe(false);
        expect(ranChangingTool([step({ tool: 'write_file', refused: true })])).toBe(false);
        expect(ranChangingTool([step({ tool: 'write_file', timedOut: true })])).toBe(false);
        expect(ranChangingTool([step({ tool: 'write_file', success: false })])).toBe(false);
        expect(ranChangingTool([step({ type: 'status', label: 'write_file' })])).toBe(false);
        expect(ranChangingTool(undefined)).toBe(false);
    });
});

describe('UndoHistorySettings', () => {
    it('shows the limits and usage, and saves new limits', async () => {
        vi.spyOn(api, 'getUndoSettings').mockResolvedValue({
            retention_days: 7, max_mb: 500, used_bytes: 3 * 1024 * 1024, turns: 4, path: 'C:\\Users\\me\\.gaia\\snapshots',
        });
        const save = vi.spyOn(api, 'updateUndoSettings').mockResolvedValue({
            retention_days: 3, max_mb: 500, used_bytes: 1024, turns: 1, path: 'C:\\Users\\me\\.gaia\\snapshots',
        });
        render(<UndoHistorySettings />);
        expect(await screen.findByText(/3.0 MB for 4 turns/)).toBeInTheDocument();
        const save_btn = screen.getByRole('button', { name: 'Save' });
        expect(save_btn).toBeDisabled();
        fireEvent.change(screen.getByLabelText('Keep for (days)'), { target: { value: '3' } });
        fireEvent.click(save_btn);
        await waitFor(() => expect(save).toHaveBeenCalledWith(3, 500));
        expect(await screen.findByRole('status')).toHaveTextContent('Saved');
    });
});
