// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FolderChip, SessionFolderChip } from '../FolderChip';
import { useSessionEnvironment } from '../../hooks/useSessionEnvironment';
import type { SessionEnvironment } from '../../types/sessionPanel';

vi.mock('../../hooks/useSessionEnvironment');

function withEnv(env: SessionEnvironment | null): void {
    vi.mocked(useSessionEnvironment).mockReturnValue({ env, error: null, refresh: vi.fn(), change: vi.fn() } as never);
}

const ENV: SessionEnvironment = {
    session_id: 's1',
    folders: [
        { path: 'C:\\Users\\k\\.gaia\\documents', kind: 'folder', source: 'gaia_documents', removable: false },
        { path: 'C:\\Users\\k\\Work\\gaia', kind: 'folder', source: 'working_folder', removable: false },
    ],
    removed: [],
    internet: true,
    enforced: true,
};

beforeEach(() => vi.clearAllMocks());

describe('FolderChip', () => {
    it('names the folder and gives its full path on hover', () => {
        render(<FolderChip path="/home/u/my-project" name="my-project" />);
        expect(screen.getByText('my-project').parentElement).toHaveAttribute('title', 'GAIA works in /home/u/my-project');
    });

    it('says when GAIA works in its own folder', () => {
        render(<FolderChip path="/home/u/.gaia/documents" name="documents" gaiaFolder />);
        expect(screen.getByText('documents').parentElement!.getAttribute('title')).toMatch(/its own folder/);
    });

    it('is only a label without somewhere to open', () => {
        render(<FolderChip path="/p/x" name="x" />);
        expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });
});

describe('SessionFolderChip', () => {
    it("shows the chat's working folder by name, on Windows paths too", () => {
        withEnv(ENV);
        render(<SessionFolderChip sessionId="s1" onOpen={vi.fn()} />);
        expect(screen.getByRole('button', { name: /Working folder gaia/ })).toHaveAttribute('title', 'GAIA works in C:\\Users\\k\\Work\\gaia');
    });

    it('opens where the folder can be changed', () => {
        withEnv(ENV);
        const onOpen = vi.fn();
        render(<SessionFolderChip sessionId="s1" onOpen={onOpen} />);
        fireEvent.click(screen.getByRole('button', { name: /Working folder gaia/ }));
        expect(onOpen).toHaveBeenCalledOnce();
    });

    it('shows nothing until the environment is known, or when the chat has no working folder', () => {
        withEnv(null);
        const { container, rerender } = render(<SessionFolderChip sessionId="s1" onOpen={vi.fn()} />);
        expect(container).toBeEmptyDOMElement();
        withEnv({ ...ENV, folders: ENV.folders.slice(0, 1) });
        rerender(<SessionFolderChip sessionId="s1" onOpen={vi.fn()} />);
        expect(container).toBeEmptyDOMElement();
    });
});
