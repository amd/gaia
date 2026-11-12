// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** "Browse files" in the library used to drop you in the chat when the file browser closed (#7421). */

import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FileBrowser } from '../FileBrowser';
import { DocumentLibrary } from '../DocumentLibrary';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { BrowseResponse } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const BROWSE: BrowseResponse = {
    current_path: '/home/user/docs',
    parent_path: '/home/user',
    entries: [{ name: 'notes.txt', path: '/home/user/docs/notes.txt', type: 'file', size: 512, extension: '.txt', modified: '' }],
    quick_links: [],
};

const DOC = {
    id: 'doc-1', filename: 'notes.txt', filepath: '/home/user/docs/notes.txt', file_size: 512,
    chunk_count: 1, indexed_at: '', last_accessed_at: null, sessions_using: 1, indexing_status: 'complete',
};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.browseFiles.mockResolvedValue(BROWSE);
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    useChatStore.setState({
        currentSessionId: 'session-1',
        sessions: [{ id: 'session-1', title: 'Chat', document_ids: [] } as never],
        documents: [],
        showDocLibrary: true,
        showFileBrowser: false,
        fileBrowserFromLibrary: false,
    });
});

function openBrowserFromLibrary() {
    render(<DocumentLibrary />);
    fireEvent.click(screen.getByRole('button', { name: /Browse files/ }));
    const state = useChatStore.getState();
    expect(state.showDocLibrary).toBe(false);
    expect(state.showFileBrowser).toBe(true);
}

describe('the file browser opened from the library', () => {
    it('goes back to the library when closed', () => {
        openBrowserFromLibrary();

        useChatStore.getState().setShowFileBrowser(false);

        expect(useChatStore.getState().showDocLibrary).toBe(true);
        expect(useChatStore.getState().fileBrowserFromLibrary).toBe(false);
    });

    it('goes back to the library once the picked files are added', async () => {
        mockedApi.uploadDocumentByPath.mockResolvedValue(DOC as never);
        mockedApi.attachDocument.mockResolvedValue(undefined as never);
        openBrowserFromLibrary();
        render(<FileBrowser onStartChat={vi.fn()} />);

        const row = (await screen.findByText('notes.txt')).closest('.fb-entry') as HTMLElement;
        fireEvent.click(within(row).getByRole('checkbox'));
        fireEvent.click(screen.getByRole('button', { name: /Index Selected/i }));

        await waitFor(() => expect(useChatStore.getState().showDocLibrary).toBe(true));
        expect(useChatStore.getState().showFileBrowser).toBe(false);
    });

    it('does not reopen the library when it starts a chat about the files', async () => {
        mockedApi.uploadDocumentByPath.mockResolvedValue(DOC as never);
        mockedApi.attachDocument.mockResolvedValue(undefined as never);
        openBrowserFromLibrary();
        render(<FileBrowser onStartChat={vi.fn()} />);

        const row = (await screen.findByText('notes.txt')).closest('.fb-entry') as HTMLElement;
        fireEvent.click(within(row).getByRole('checkbox'));
        fireEvent.click(screen.getByRole('button', { name: /Ask Agent/i }));

        await waitFor(() => expect(useChatStore.getState().showFileBrowser).toBe(false));
        expect(useChatStore.getState().showDocLibrary).toBe(false);
    });
});

it('opened from anywhere else, closing just closes it', () => {
    useChatStore.setState({ showDocLibrary: false, showFileBrowser: true });

    useChatStore.getState().setShowFileBrowser(false);

    expect(useChatStore.getState().showDocLibrary).toBe(false);
});
