// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentLibrary, REMOVE_UNDO_MS } from '../DocumentLibrary';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { Document } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const NOTES: Document = {
    id: 'd1',
    filename: 'garden-notes.txt',
    filepath: '/tmp/garden-notes.txt',
    file_size: 7270,
    chunk_count: 7,
    indexed_at: new Date().toISOString(),
    last_accessed_at: null,
    sessions_using: 0,
    indexing_status: 'complete',
};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.listDocuments.mockResolvedValue({ documents: [NOTES], total: 1, total_size_bytes: 7270, total_chunks: 7 });
    mockedApi.deleteDocument.mockResolvedValue(undefined as never);
    useChatStore.setState({ currentSessionId: null, sessions: [], documents: [], showDocLibrary: true, draftDocumentIds: [] });
});

afterEach(() => {
    vi.useRealTimers();
});

/** Lets the Undo window run out, then hands the clock back for the async checks. */
function waitOutUndo() {
    act(() => { vi.advanceTimersByTime(REMOVE_UNDO_MS); });
    vi.useRealTimers();
}

describe('removing a document', () => {
    it('hides the row, says the file is untouched and waits before deleting', async () => {
        render(<DocumentLibrary />);
        await screen.findByText('garden-notes.txt');
        vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });

        fireEvent.click(screen.getByRole('button', { name: 'Remove garden-notes.txt' }));

        expect(screen.getByRole('status')).toHaveTextContent('Removed garden-notes.txt from your library. The file on your PC is untouched.');
        expect(screen.queryByText('garden-notes.txt')).toBeNull();
        expect(mockedApi.deleteDocument).not.toHaveBeenCalled();

        waitOutUndo();

        await waitFor(() => expect(mockedApi.deleteDocument).toHaveBeenCalledWith('d1'));
        await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    });

    it('puts the row back on Undo and never deletes it', async () => {
        render(<DocumentLibrary />);
        await screen.findByText('garden-notes.txt');
        vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });

        fireEvent.click(screen.getByRole('button', { name: 'Remove garden-notes.txt' }));
        fireEvent.click(screen.getByRole('button', { name: /Undo/ }));
        act(() => { vi.advanceTimersByTime(REMOVE_UNDO_MS * 2); });

        expect(screen.getByText('garden-notes.txt')).toBeInTheDocument();
        expect(mockedApi.deleteDocument).not.toHaveBeenCalled();
    });

    it('still deletes when the library closes before Undo runs out', async () => {
        const { unmount } = render(<DocumentLibrary />);
        await screen.findByText('garden-notes.txt');

        fireEvent.click(screen.getByRole('button', { name: 'Remove garden-notes.txt' }));
        unmount();

        await waitFor(() => expect(mockedApi.deleteDocument).toHaveBeenCalledWith('d1'));
    });
});
