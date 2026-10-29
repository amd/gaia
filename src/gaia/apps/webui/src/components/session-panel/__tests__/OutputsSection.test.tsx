// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Outputs (#5277): everything the chat produced, newest first, with safe actions and undo states. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { OutputsSection, OUTPUTS_SHOWN, whenText } from '../OutputsSection';
import { announceUndo } from '../../../utils/undoEvents';
import type { OutputItem } from '../../../types/sessionPanel';
import { resetPanelState, SID, stubPanelApi } from './testUtils';

vi.mock('../../../services/api');

let mocked: ReturnType<typeof stubPanelApi>;

function item(over: Partial<OutputItem>): OutputItem {
    return {
        id: over.name ?? 'x',
        kind: 'file',
        name: 'x',
        path: `C:\\work\\${over.name ?? 'x'}`,
        detail: null,
        message_id: 2,
        created_at: '2026-10-16 20:00:00',
        change: null,
        state: 'present',
        openable: true,
        revealable: true,
        ...over,
    };
}

beforeEach(() => {
    vi.clearAllMocks();
    mocked = stubPanelApi();
    resetPanelState();
    mocked.openFileInApp.mockResolvedValue({ status: 'ok', path: '' });
    mocked.openFileOrFolder.mockResolvedValue({ status: 'ok', path: '' });
});

function rows() {
    return within(screen.getByRole('list', { name: 'What this chat produced' })).getAllByRole('listitem');
}

describe('OutputsSection', () => {
    it('lists each kind with its type and turn, in the order the server sent (newest first)', async () => {
        mocked.getOutputs.mockResolvedValue({
            session_id: SID,
            items: [
                item({ name: 'summary.csv', kind: 'export', change: 'created', message_id: 6 }),
                item({ name: 'cat.png', kind: 'image', message_id: 6 }),
                item({ name: 'Likes tea', kind: 'memory', path: null, openable: false, revealable: false, message_id: 4 }),
                item({ name: 'Morning brief', kind: 'schedule', path: null, openable: false, revealable: false, message_id: 4 }),
                item({ name: 'manual.pdf', kind: 'document', message_id: null }),
            ],
        });
        render(<OutputsSection sessionId={SID} busy={false} onJumpToMessage={() => true} />);
        await waitFor(() => expect(rows()).toHaveLength(5));
        const r = rows();
        expect(r[0]).toHaveTextContent('summary.csv');
        expect(r[0]).toHaveTextContent('Export created');
        expect(r[1]).toHaveTextContent('Image');
        expect(r[2]).toHaveTextContent('Memory saved');
        expect(r[3]).toHaveTextContent('Scheduled task');
        expect(r[4]).toHaveTextContent('Indexed document');
        // Added from the document bar, not by a turn: nothing to jump to.
        expect(within(r[4]).queryByRole('button', { name: /Go to the turn/ })).not.toBeInTheDocument();
    });

    it('opens documents, but only shows scripts and web pages in their folder', async () => {
        mocked.getOutputs.mockResolvedValue({
            session_id: SID,
            items: [
                item({ name: 'notes.md' }),
                item({ name: 'build.py', openable: false }),
                item({ name: 'page.html', openable: false }),
            ],
        });
        render(<OutputsSection sessionId={SID} busy={false} onJumpToMessage={() => true} />);
        await waitFor(() => expect(rows()).toHaveLength(3));
        const [md, py, html] = rows();
        expect(within(py).queryByRole('button', { name: 'Open' })).not.toBeInTheDocument();
        expect(within(html).queryByRole('button', { name: 'Open' })).not.toBeInTheDocument();
        fireEvent.click(within(md).getByRole('button', { name: 'Open' }));
        expect(mocked.openFileInApp).toHaveBeenCalledWith('C:\\work\\notes.md');
        fireEvent.click(within(py).getByRole('button', { name: /Show in folder/ }));
        expect(mocked.openFileOrFolder).toHaveBeenCalledWith('C:\\work\\build.py', true);
    });

    it('shows undone and deleted-outside-GAIA items without actions', async () => {
        mocked.getOutputs.mockResolvedValue({
            session_id: SID,
            items: [
                item({ name: 'a.md', state: 'undone', change: 'created', openable: false, revealable: false }),
                item({ name: 'b.md', state: 'gone', openable: false, revealable: false }),
            ],
        });
        render(<OutputsSection sessionId={SID} busy={false} onJumpToMessage={() => true} />);
        await waitFor(() => expect(rows()).toHaveLength(2));
        const [a, b] = rows();
        expect(a).toHaveTextContent('Undone');
        expect(b).toHaveTextContent('No longer on disk');
        for (const r of [a, b]) {
            expect(within(r).queryByRole('button', { name: 'Open' })).not.toBeInTheDocument();
            expect(within(r).queryByRole('button', { name: /Show in folder/ })).not.toBeInTheDocument();
            expect(r).toHaveClass('is-inactive');
        }
    });

    it('jumps to the turn, and says so when that turn is not loaded', async () => {
        mocked.getOutputs.mockResolvedValue({ session_id: SID, items: [item({ name: 'r.md', message_id: 12 })] });
        const jump = vi.fn().mockReturnValueOnce(true).mockReturnValueOnce(false);
        render(<OutputsSection sessionId={SID} busy={false} onJumpToMessage={jump} />);
        const go = await screen.findByRole('button', { name: 'Go to the turn that made r.md' });
        fireEvent.click(go);
        expect(jump).toHaveBeenCalledWith(12);
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        fireEvent.click(go);
        expect(screen.getByRole('alert')).toHaveTextContent("isn't loaded");
    });

    it(`shows the first ${OUTPUTS_SHOWN}, then the rest on request`, async () => {
        mocked.getOutputs.mockResolvedValue({
            session_id: SID,
            items: Array.from({ length: 8 }, (_, i) => item({ name: `f${i}.md` })),
        });
        render(<OutputsSection sessionId={SID} busy={false} onJumpToMessage={() => true} />);
        await waitFor(() => expect(rows()).toHaveLength(OUTPUTS_SHOWN));
        fireEvent.click(screen.getByRole('button', { name: 'Show 3 more' }));
        expect(rows()).toHaveLength(8);
        fireEvent.click(screen.getByRole('button', { name: 'Show fewer' }));
        expect(rows()).toHaveLength(OUTPUTS_SHOWN);
    });

    it('re-reads after a reply ends and after an Undo', async () => {
        const { rerender } = render(<OutputsSection sessionId={SID} busy={false} onJumpToMessage={() => true} />);
        await waitFor(() => expect(mocked.getOutputs).toHaveBeenCalledTimes(1));
        rerender(<OutputsSection sessionId={SID} busy onJumpToMessage={() => true} />);
        rerender(<OutputsSection sessionId={SID} busy={false} onJumpToMessage={() => true} />);
        await waitFor(() => expect(mocked.getOutputs).toHaveBeenCalledTimes(2));
        act(() => announceUndo('another-chat'));
        expect(mocked.getOutputs).toHaveBeenCalledTimes(2);
        act(() => announceUndo(SID));
        await waitFor(() => expect(mocked.getOutputs).toHaveBeenCalledTimes(3));
    });

    it('says so when the chat produced nothing yet', async () => {
        render(<OutputsSection sessionId={SID} busy={false} onJumpToMessage={() => true} />);
        expect(await screen.findByText(/Nothing yet/)).toBeInTheDocument();
    });

    it('reports a failed read instead of claiming nothing was produced', async () => {
        mocked.getOutputs.mockRejectedValue(new Error('disk unreadable'));
        render(<OutputsSection sessionId={SID} busy={false} onJumpToMessage={() => true} />);
        expect(await screen.findByText(/Couldn't list what this chat produced: disk unreadable/)).toBeInTheDocument();
        expect(screen.queryByText(/Nothing yet/)).not.toBeInTheDocument();
    });
});

describe('whenText', () => {
    const now = Date.parse('2026-10-16T20:10:00Z');
    it('reads UTC times relative to now', () => {
        expect(whenText('2026-10-16 20:09:40', now)).toBe('just now');
        expect(whenText('2026-10-16 20:00:00', now)).toBe('10 min ago');
        expect(whenText('2026-10-16 17:00:00', now)).toBe('3 h ago');
        expect(whenText(null, now)).toBe('');
        expect(whenText('garbage', now)).toBe('');
    });
});
