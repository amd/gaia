// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Changes (#5278): totals, review, Undo all and git, with nothing run unconfirmed. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChangesSection, totalText } from '../ChangesSection';
import type { ChatFileChange, GitRepo, SessionChanges } from '../../../types/sessionPanel';
import { NO_CHANGES, resetPanelState, SID, stubPanelApi } from './testUtils';

vi.mock('../../../services/api');

let mocked: ReturnType<typeof stubPanelApi>;

function file(over: Partial<ChatFileChange>): ChatFileChange {
    return {
        path: 'C:\\repo\\app.py',
        change: 'edited',
        unified: '--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n',
        binary: false,
        too_large: false,
        truncated: false,
        additions: 1,
        deletions: 1,
        before_size: 6,
        after_size: 6,
        message_ids: [2, 4],
        changed_since: null,
        ...over,
    };
}

const CHANGES: SessionChanges = {
    files: [
        file({}),
        file({ path: 'C:\\repo\\util.py', change: 'created', additions: 12, deletions: 0, message_ids: [4] }),
        file({ path: 'C:\\repo\\old.txt', change: 'deleted', additions: 0, deletions: 3, message_ids: [2] }),
    ],
    totals: { files: 3, additions: 13, deletions: 4, uncounted: 0 },
    turns: 2,
    pending: false,
    untracked: [],
    incomplete: false,
};

const REPO: GitRepo = {
    root: 'C:\\repo',
    branch: 'feature/totals',
    remote: 'origin',
    default_branch: 'main',
    gh: true,
    files: ['app.py', 'util.py', 'old.txt'],
    uncommitted: ['app.py', 'util.py', 'old.txt'],
    ignored: [],
    draft: { title: 'Add a helper for the totals', body: 'Files:\n- app.py\n- util.py\n- old.txt' },
    pr_blocked: null,
};

function conflictError(detail: string) {
    // The real ApiError is mocked away; the section only reads these fields.
    return Object.assign(new Error(detail), { detail, reason: 'conflict', httpStatus: 409 });
}

beforeEach(() => {
    vi.clearAllMocks();
    mocked = stubPanelApi();
    resetPanelState();
    mocked.getSessionChanges.mockResolvedValue(CHANGES);
});

function renderSection(onJump = vi.fn(() => true), busy = false) {
    return render(<ChangesSection sessionId={SID} busy={busy} onJumpToMessage={onJump} />);
}

describe('ChangesSection', () => {
    it('shows nothing for a chat that changed no files', async () => {
        mocked.getSessionChanges.mockResolvedValue(NO_CHANGES);
        const { container } = renderSection();
        await waitFor(() => expect(mocked.getSessionChanges).toHaveBeenCalled());
        expect(container).toBeEmptyDOMElement();
    });

    it('totals the chat and lists each file with its lines and turns', async () => {
        renderSection();
        expect(await screen.findByText('3 files changed, +13 −4')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /^Changes/ })).toHaveTextContent('3 files · +13 −4');
        expect(screen.getByText('app.py').closest('li')).toHaveTextContent('Edited · +1 −1 · 2 turns');
        expect(screen.getByText('util.py').closest('li')).toHaveTextContent('Created');
        expect(screen.getByText('old.txt').closest('li')).toHaveTextContent('Deleted');
    });

    it('jumps to the last turn that changed a file', async () => {
        const jump = vi.fn(() => true);
        renderSection(jump);
        fireEvent.click(await screen.findByRole('button', { name: 'Go to the last turn that changed app.py' }));
        expect(jump).toHaveBeenCalledWith(4);
    });

    it('flags a file changed outside GAIA since', async () => {
        mocked.getSessionChanges.mockResolvedValue({
            ...CHANGES,
            files: [file({ changed_since: 'changed after this turn' })],
        });
        renderSection();
        expect(await screen.findByText(/Changed outside GAIA: changed after this turn/)).toBeInTheDocument();
    });

    it('reviews every file as one diff across turns', async () => {
        renderSection();
        fireEvent.click(await screen.findByRole('button', { name: /Review changes/ }));
        const dialog = screen.getByRole('dialog', { name: "Review this chat's changes" });
        expect(dialog).toHaveTextContent('C:\\repo\\app.py');
        expect(dialog).toHaveTextContent('x = 2');
        expect(dialog).toHaveTextContent('C:\\repo\\util.py');
    });

    it('undoes everything only after confirmation', async () => {
        mocked.undoAllChanges.mockResolvedValue({
            turn_ids: ['t1', 't2'], restored: ['a', 'b'], removed: ['c'], kept_folders: [], untracked: [], incomplete: false,
        });
        mocked.listTurnChanges.mockResolvedValue({ turns: [] });
        const undone: string[] = [];
        const onUndo = (e: Event) => undone.push((e as CustomEvent<string>).detail);
        window.addEventListener('gaia:changes-undone', onUndo);
        renderSection();
        fireEvent.click(await screen.findByRole('button', { name: /Undo all/ }));
        expect(mocked.undoAllChanges).not.toHaveBeenCalled();
        const confirm = screen.getByRole('group', { name: 'Confirm undo all' });
        expect(confirm).toHaveTextContent('Put 3 files back');
        fireEvent.click(within(confirm).getByRole('button', { name: 'Undo all' }));
        await waitFor(() => expect(mocked.undoAllChanges).toHaveBeenCalledWith(SID, false));
        expect(await screen.findByRole('status')).toHaveTextContent('Undone: restored 2 files, removed 1 new file.');
        expect(undone).toEqual([SID]);
        expect(mocked.listTurnChanges).toHaveBeenCalledWith(SID);
        window.removeEventListener('gaia:changes-undone', onUndo);
        // The list empties after Undo all; the result stays readable.
        mocked.getSessionChanges.mockResolvedValue(NO_CHANGES);
        act(() => { window.dispatchEvent(new CustomEvent('gaia:changes-undone', { detail: SID })); });
        await waitFor(() => expect(screen.queryByText('app.py')).not.toBeInTheDocument());
        expect(screen.getByRole('status')).toHaveTextContent('Undone: restored 2 files');
    });

    it('cancelling Undo all runs nothing', async () => {
        renderSection();
        fireEvent.click(await screen.findByRole('button', { name: /Undo all/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(mocked.undoAllChanges).not.toHaveBeenCalled();
    });

    it('on a conflict, offers to undo anyway and only then forces', async () => {
        mocked.undoAllChanges
            .mockRejectedValueOnce(conflictError('1 file(s) changed after this chat last changed them: app.py.'))
            .mockResolvedValueOnce({ turn_ids: [], restored: ['app.py'], removed: [], kept_folders: [], untracked: [], incomplete: false });
        mocked.listTurnChanges.mockResolvedValue({ turns: [] });
        renderSection();
        fireEvent.click(await screen.findByRole('button', { name: /Undo all/ }));
        fireEvent.click(within(screen.getByRole('group', { name: 'Confirm undo all' })).getByRole('button', { name: 'Undo all' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('changed after this chat last changed them');
        expect(mocked.undoAllChanges).toHaveBeenCalledTimes(1);
        fireEvent.click(screen.getByRole('button', { name: 'Undo anyway and lose those changes' }));
        await waitFor(() => expect(mocked.undoAllChanges).toHaveBeenLastCalledWith(SID, true));
    });

    it("can't undo while a reply is running", async () => {
        renderSection(vi.fn(() => true), true);
        expect(await screen.findByRole('button', { name: /Undo all/ })).toBeDisabled();
    });

    it('notes commands that may have changed files it cannot see', async () => {
        mocked.getSessionChanges.mockResolvedValue({
            ...CHANGES,
            incomplete: true,
            untracked: [
                { kind: 'run_python', detail: 'save totals', paths: [] },
                { kind: 'shell', detail: 'npm run build', paths: [] },
                { kind: 'run_python', detail: 'write the chart', paths: [] },
            ],
        });
        renderSection();
        expect(await screen.findByRole('note')).toHaveTextContent(
            "In this chat, GAIA ran 2 Python snippets and 1 command it can't track. If they changed any files, Undo all won't restore them.",
        );
    });

    it('re-reads totals when a reply ends', async () => {
        const { rerender } = renderSection();
        await waitFor(() => expect(mocked.getSessionChanges).toHaveBeenCalledTimes(1));
        rerender(<ChangesSection sessionId={SID} busy onJumpToMessage={() => true} />);
        rerender(<ChangesSection sessionId={SID} busy={false} onJumpToMessage={() => true} />);
        await waitFor(() => expect(mocked.getSessionChanges).toHaveBeenCalledTimes(2));
    });
});

describe('ChangesSection git', () => {
    beforeEach(() => {
        mocked.getSessionGit.mockResolvedValue({ available: true, repos: [REPO] });
    });

    it('commits only after the drafted message is shown and confirmed, with the user’s edits', async () => {
        mocked.commitChanges.mockResolvedValue({ root: REPO.root, commit: 'abc1234', files: ['app.py', 'util.py', 'old.txt'], ignored: [] });
        renderSection();
        fireEvent.click(await screen.findByRole('button', { name: /Commit…/ }));
        expect(mocked.commitChanges).not.toHaveBeenCalled();
        const dialog = screen.getByRole('dialog', { name: 'Commit this chat’s changes' });
        const summary = within(dialog).getByLabelText('Summary');
        expect(summary).toHaveValue('Add a helper for the totals');
        fireEvent.change(summary, { target: { value: 'Add totals helper' } });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Commit 3 files' }));
        await waitFor(() => expect(mocked.commitChanges).toHaveBeenCalledWith(
            SID, 'C:\\repo', 'Add totals helper\n\nFiles:\n- app.py\n- util.py\n- old.txt',
        ));
        expect(await screen.findByText('Committed 3 files as abc1234.')).toBeInTheDocument();
    });

    it('names the files git ignores as left out of the commit', async () => {
        mocked.getSessionGit.mockResolvedValue({
            available: true,
            repos: [{ ...REPO, files: ['app.py'], uncommitted: ['app.py'], ignored: ['out/summary.csv'] }],
        });
        renderSection();
        fireEvent.click(await screen.findByRole('button', { name: /Commit…/ }));
        const dialog = screen.getByRole('dialog', { name: 'Commit this chat’s changes' });
        expect(within(dialog).getByText(/Left out, because git ignores it: out\/summary\.csv/)).toBeInTheDocument();
        expect(within(dialog).getByRole('button', { name: 'Commit 1 file' })).toBeInTheDocument();
    });

    it('a repo whose only changes are ignored is not shown as committed', async () => {
        mocked.getSessionGit.mockResolvedValue({
            available: true,
            repos: [{ ...REPO, files: [], uncommitted: [], ignored: ['build/a.bin'] }],
        });
        renderSection();
        expect(await screen.findByRole('button', { name: /Commit…/ })).toBeDisabled();
        expect(screen.queryByText(/· committed/)).not.toBeInTheDocument();
    });

    it('closing the commit dialog runs nothing', async () => {
        renderSection();
        fireEvent.click(await screen.findByRole('button', { name: /Commit…/ }));
        fireEvent.click(within(screen.getByRole('dialog', { name: 'Commit this chat’s changes' })).getByRole('button', { name: 'Cancel' }));
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        expect(mocked.commitChanges).not.toHaveBeenCalled();
    });

    it('opens a pull request only after confirmation, and shows where', async () => {
        mocked.createPullRequest.mockResolvedValue({ root: REPO.root, url: 'https://github.com/o/r/pull/7' });
        renderSection();
        fireEvent.click(await screen.findByRole('button', { name: /Create PR…/ }));
        const dialog = screen.getByRole('dialog', { name: 'Create a pull request' });
        expect(dialog).toHaveTextContent('Pushes feature/totals to origin (never forced)');
        expect(mocked.createPullRequest).not.toHaveBeenCalled();
        fireEvent.click(within(dialog).getByRole('button', { name: 'Push and open PR' }));
        await waitFor(() => expect(mocked.createPullRequest).toHaveBeenCalledWith(SID, 'C:\\repo', REPO.draft.title, REPO.draft.body));
        expect(await screen.findByText('Opened https://github.com/o/r/pull/7')).toBeInTheDocument();
    });

    it('explains why a PR cannot be opened from here', async () => {
        mocked.getSessionGit.mockResolvedValue({
            available: true,
            repos: [{ ...REPO, branch: 'main', pr_blocked: "You're on main. Switch to a new branch to open a pull request." }],
        });
        renderSection();
        expect(await screen.findByRole('button', { name: /Create PR…/ })).toBeDisabled();
        expect(screen.getByText(/Switch to a new branch/)).toBeInTheDocument();
    });

    it('shows a refused commit in the dialog and keeps it open', async () => {
        mocked.commitChanges.mockRejectedValue(new Error('`git commit -F` failed: pre-commit hook failed'));
        renderSection();
        fireEvent.click(await screen.findByRole('button', { name: /Commit…/ }));
        const dialog = screen.getByRole('dialog', { name: 'Commit this chat’s changes' });
        await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Commit 3 files' })); });
        expect(within(dialog).getByRole('alert')).toHaveTextContent('pre-commit hook failed');
    });

    it('offers nothing when the changed files are not in a git repository', async () => {
        mocked.getSessionGit.mockResolvedValue({ available: true, repos: [] });
        renderSection();
        await screen.findByText('3 files changed, +13 −4');
        expect(screen.queryByRole('button', { name: /Commit…/ })).not.toBeInTheDocument();
    });
});

describe('totalText', () => {
    it('reads like a sentence', () => {
        expect(totalText(CHANGES)).toBe('3 files changed, +13 −4');
        expect(totalText({ ...CHANGES, totals: { files: 1, additions: 0, deletions: 2, uncounted: 0 } })).toBe('1 file changed, +0 −2');
    });
});
