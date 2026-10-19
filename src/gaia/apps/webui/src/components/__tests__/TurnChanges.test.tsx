// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** "Undo this turn" / "Review changes" under an answer (#5225). */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TurnChanges } from '../TurnChanges';
import { DiffCard } from '../render/DiffCard';
import { UndoHistorySettings } from '../settings/UndoHistorySettings';
import { resetTurnChangesCache } from '../../hooks/useTurnChanges';
import * as api from '../../services/api';
import type { TurnChanges as Turn } from '../../types/changes';

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
        expect(await screen.findByText(/undone/)).toBeInTheDocument();
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
        expect(await screen.findByText(/Undo may be incomplete/)).toBeInTheDocument();
        expect(screen.getByText('Command: npm run build')).toBeInTheDocument();
    });

    it('a listing error is shown, not swallowed', async () => {
        vi.spyOn(api, 'listTurnChanges').mockRejectedValue(new Error('backend down'));
        render(<TurnChanges sessionId="s1" messageId={7} />);
        expect(await screen.findByRole('note')).toHaveTextContent('backend down');
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
});

describe('DiffCard', () => {
    it('drops the CR of CRLF lines and the trailing empty line', () => {
        const { container } = render(<DiffCard data={{ unified: '-a\r\n+b\r\n' }} />);
        const lines = Array.from(container.querySelectorAll('.render-diff__line')).map((el) => el.textContent);
        expect(lines).toEqual(['-a', '+b']);
    });

    it('shows a note and badge, and no body when there is no text diff', () => {
        const { container } = render(<DiffCard data={{ title: 'x.bin', badge: 'Deleted', note: 'Binary file', unified: '' }} />);
        expect(screen.getByText('Deleted')).toBeInTheDocument();
        expect(screen.getByText('Binary file')).toBeInTheDocument();
        expect(container.querySelector('.render-diff__body')).toBeNull();
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
