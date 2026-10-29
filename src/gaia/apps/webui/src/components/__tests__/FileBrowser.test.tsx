// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FileBrowser } from '../FileBrowser';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { BrowseResponse } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const BROWSE_RESPONSE: BrowseResponse = {
    current_path: '/home/user/docs',
    parent_path: '/home/user',
    entries: [
        { name: 'report.pdf', path: '/home/user/docs/report.pdf', type: 'file', size: 1024, extension: '.pdf', modified: '' },
        { name: 'notes.txt', path: '/home/user/docs/notes.txt', type: 'file', size: 512, extension: '.txt', modified: '' },
    ],
    quick_links: [],
};

const FILE_PREVIEW = {
    path: '/home/user/docs/report.pdf',
    name: 'report.pdf',
    size: 1024,
    size_display: '1 KB',
    extension: '.pdf',
    modified: '',
    is_text: true,
    preview_lines: ['line 1', 'line 2'],
    total_lines: 2,
    columns: null,
    row_count: null,
};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.browseFiles.mockResolvedValue(BROWSE_RESPONSE);
    mockedApi.previewFile.mockResolvedValue(FILE_PREVIEW);

    useChatStore.setState({
        currentSessionId: 'session-1',
        sessions: [],
        showFileBrowser: true,
    });
});

it('closes on Escape', async () => {
    render(<FileBrowser onStartChat={vi.fn()} />);
    await screen.findByText('report.pdf');

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(useChatStore.getState().showFileBrowser).toBe(false);
});

describe('FileBrowser row selection', () => {
    it('clicking a file row selects it and enables Index Selected and Ask Agent', async () => {
        render(<FileBrowser onStartChat={vi.fn()} />);

        // Wait for the directory listing to load
        const fileName = await screen.findByText('report.pdf');
        const row = fileName.closest('.fb-entry') as HTMLElement;
        expect(row).toBeTruthy();

        const indexBtn = screen.getByRole('button', { name: /Index Selected/i });
        const askBtn = screen.getByRole('button', { name: /Ask Agent/i });

        // Both buttons disabled before selection
        expect(indexBtn).toBeDisabled();
        expect(askBtn).toBeDisabled();

        // Click the row to select
        fireEvent.click(row);

        // After clicking the row the file should be selected
        await waitFor(() => {
            expect(indexBtn).not.toBeDisabled();
            expect(askBtn).not.toBeDisabled();
        });

        // The checkbox inside the row should now be checked
        const checkbox = within(row).getByRole('checkbox');
        expect(checkbox).toBeChecked();
    });

    it('clicking the preview icon previews without selecting the file', async () => {
        render(<FileBrowser onStartChat={vi.fn()} />);

        const fileName = await screen.findByText('report.pdf');
        const row = fileName.closest('.fb-entry') as HTMLElement;
        expect(row).toBeTruthy();

        const indexBtn = screen.getByRole('button', { name: /Index Selected/i });
        const askBtn = screen.getByRole('button', { name: /Ask Agent/i });

        // Click the preview button inside the row
        const previewBtn = within(row).getByRole('button', { name: /Preview/i });
        fireEvent.click(previewBtn);

        // previewFile should have been called
        await waitFor(() => {
            expect(mockedApi.previewFile).toHaveBeenCalledWith('/home/user/docs/report.pdf');
        });

        // Checkbox should stay unchecked
        const checkbox = within(row).getByRole('checkbox');
        expect(checkbox).not.toBeChecked();

        // Buttons should still be disabled (no selection happened)
        expect(indexBtn).toBeDisabled();
        expect(askBtn).toBeDisabled();
    });

    it('checkbox toggles selection on and off (regression guard)', async () => {
        render(<FileBrowser onStartChat={vi.fn()} />);

        const fileName = await screen.findByText('notes.txt');
        const row = fileName.closest('.fb-entry') as HTMLElement;
        expect(row).toBeTruthy();

        const indexBtn = screen.getByRole('button', { name: /Index Selected/i });
        const askBtn = screen.getByRole('button', { name: /Ask Agent/i });

        const checkbox = within(row).getByRole('checkbox');

        // Initially unchecked, buttons disabled
        expect(checkbox).not.toBeChecked();
        expect(indexBtn).toBeDisabled();
        expect(askBtn).toBeDisabled();

        // Click checkbox to select
        fireEvent.click(checkbox);

        await waitFor(() => {
            expect(checkbox).toBeChecked();
            expect(indexBtn).not.toBeDisabled();
            expect(askBtn).not.toBeDisabled();
        });

        // Click checkbox again to deselect
        fireEvent.click(checkbox);

        await waitFor(() => {
            expect(checkbox).not.toBeChecked();
            expect(indexBtn).toBeDisabled();
            expect(askBtn).toBeDisabled();
        });
    });
});

describe('FileBrowser search truncation', () => {
    const runSearch = async (truncated: boolean) => {
        mockedApi.searchFiles.mockResolvedValue({
            results: [
                { name: 'report.pdf', path: '/home/user/docs/report.pdf', size: 1024, size_display: '1 KB', extension: '.pdf', modified: '', directory: '/home/user/docs' },
            ],
            total: 1,
            query: 'report',
            searched_locations: ['/home/user/Documents'],
            truncated,
        });
        render(<FileBrowser onStartChat={vi.fn()} />);
        await screen.findByText('report.pdf');
        const input = screen.getByPlaceholderText('Search files on your PC...');
        fireEvent.change(input, { target: { value: 'report' } });
        fireEvent.keyDown(input, { key: 'Enter' });
        await screen.findByText(/Found 1 result/);
    };

    it('says the search stopped early when the backend truncated the walk', async () => {
        await runSearch(true);
        expect(screen.getByText(/Search stopped early/)).toBeInTheDocument();
    });

    it('shows no truncation note for a complete search', async () => {
        await runSearch(false);
        expect(screen.queryByText(/Search stopped early/)).not.toBeInTheDocument();
    });
});

describe('FileBrowser Index Selected', () => {
    const DOC = {
        id: 'doc-1', filename: 'notes.txt', filepath: '/home/user/docs/notes.txt', file_size: 512,
        chunk_count: 1, indexed_at: '', last_accessed_at: null, sessions_using: 1, indexing_status: 'complete',
    };

    beforeEach(() => {
        useChatStore.setState({
            sessions: [{ id: 'session-1', title: 'Chat', document_ids: [] } as never],
            documents: [],
        });
        mockedApi.uploadDocumentByPath.mockResolvedValue(DOC as never);
        mockedApi.listDocuments.mockResolvedValue({
            documents: [DOC] as never, total: 1, total_size_bytes: 512, total_chunks: 1,
        });
    });

    async function indexNotes() {
        render(<FileBrowser onStartChat={vi.fn()} />);
        const row = (await screen.findByText('notes.txt')).closest('.fb-entry') as HTMLElement;
        fireEvent.click(within(row).getByRole('checkbox'));
        fireEvent.click(screen.getByRole('button', { name: /Index Selected/i }));
        await waitFor(() => expect(mockedApi.uploadDocumentByPath).toHaveBeenCalled());
    }

    it('refreshes the document list so the chat bar shows the new document', async () => {
        mockedApi.attachDocument.mockResolvedValue(undefined as never);
        await indexNotes();
        await waitFor(() => expect(useChatStore.getState().documents.map((d) => d.id)).toEqual(['doc-1']));
        expect(useChatStore.getState().sessions[0].document_ids).toEqual(['doc-1']);
    });

    it('does not list a document on the chat when attaching it failed', async () => {
        mockedApi.attachDocument.mockRejectedValue(new Error('API 500'));
        await indexNotes();
        await waitFor(() => expect(mockedApi.attachDocument).toHaveBeenCalled());
        await waitFor(() => expect(screen.queryByText(/Indexing 1 file/)).not.toBeInTheDocument());
        expect(useChatStore.getState().sessions[0].document_ids).toEqual([]);
    });
});

describe('FileBrowser Ask Agent', () => {
    const DOC = {
        id: 'doc-1', filename: 'notes.txt', filepath: '/home/user/docs/notes.txt', file_size: 512,
        chunk_count: 1, indexed_at: '', last_accessed_at: null, sessions_using: 0, indexing_status: 'complete',
    };

    beforeEach(() => {
        useChatStore.setState({ sessions: [], documents: [], draftDocumentIds: [], pendingPrompt: null });
        mockedApi.uploadDocumentByPath.mockResolvedValue(DOC as never);
        mockedApi.attachDocument.mockResolvedValue(undefined as never);
        mockedApi.listDocuments.mockResolvedValue({
            documents: [DOC] as never, total: 1, total_size_bytes: 512, total_chunks: 1,
        });
    });

    async function askAboutNotes(onStartChat = vi.fn().mockResolvedValue(true)) {
        render(<FileBrowser onStartChat={onStartChat} />);
        const row = (await screen.findByText('notes.txt')).closest('.fb-entry') as HTMLElement;
        fireEvent.click(within(row).getByRole('checkbox'));
        fireEvent.click(screen.getByRole('button', { name: /Ask Agent/i }));
        await waitFor(() => expect(useChatStore.getState().showFileBrowser).toBe(false));
        return onStartChat;
    }

    it('starts a new chat about the file from the new-chat screen (#5924)', async () => {
        useChatStore.setState({ currentSessionId: null });
        const onStartChat = await askAboutNotes();
        expect(onStartChat).toHaveBeenCalledWith('Please analyze this document for me: notes.txt');
        // Nothing is left waiting to fire into whichever chat opens next.
        expect(useChatStore.getState().pendingPrompt).toBeNull();
        expect(useChatStore.getState().draftDocumentIds).toEqual(['doc-1']);
    });

    it('sends into the open chat and only that chat', async () => {
        const onStartChat = await askAboutNotes();
        expect(onStartChat).not.toHaveBeenCalled();
        expect(useChatStore.getState().pendingPrompt).toEqual({
            sessionId: 'session-1', text: 'Please analyze this document for me: notes.txt',
        });
    });
});

describe('FileBrowser hidden entries', () => {
    const HOME: BrowseResponse = {
        current_path: '/home/user',
        parent_path: '/home',
        entries: [
            { name: '.config', path: '/home/user/.config', type: 'folder', size: 0, extension: '', modified: '' },
            { name: '.env', path: '/home/user/.env', type: 'file', size: 10, extension: '', modified: '' },
            { name: 'Documents', path: '/home/user/Documents', type: 'folder', size: 0, extension: '', modified: '' },
        ],
        quick_links: [],
    };

    it('hides dot entries until Show hidden is turned on', async () => {
        mockedApi.browseFiles.mockResolvedValue(HOME);
        render(<FileBrowser onStartChat={vi.fn()} />);
        await screen.findByText('Documents');
        expect(screen.queryByText('.config')).toBeNull();
        expect(screen.queryByText('.env')).toBeNull();

        fireEvent.click(screen.getByRole('checkbox', { name: 'Show hidden' }));

        expect(screen.getByText('.config')).toBeTruthy();
        expect(screen.getByText('.env')).toBeTruthy();
    });

    it('drops a hidden selection when Show hidden is turned off', async () => {
        mockedApi.browseFiles.mockResolvedValue(HOME);
        render(<FileBrowser onStartChat={vi.fn()} />);
        await screen.findByText('Documents');
        const toggle = screen.getByRole('checkbox', { name: 'Show hidden' });
        fireEvent.click(toggle);
        fireEvent.click(screen.getByRole('checkbox', { name: 'Select .env' }));
        expect(screen.getByRole('button', { name: /Index Selected/i })).toBeEnabled();

        fireEvent.click(toggle);

        expect(screen.getByRole('button', { name: /Index Selected/i })).toBeDisabled();
    });

    it('explains a folder that holds only hidden items', async () => {
        mockedApi.browseFiles.mockResolvedValue({ ...HOME, entries: HOME.entries.slice(0, 2) });
        render(<FileBrowser onStartChat={vi.fn()} />);
        expect(await screen.findByText(/Only hidden items here/)).toBeTruthy();
    });
});

describe('FileBrowser hidden selections from search', () => {
    const HOME: BrowseResponse = {
        current_path: '/home/user',
        parent_path: '/home',
        entries: [
            { name: 'Documents', path: '/home/user/Documents', type: 'folder', size: 0, extension: '', modified: '' },
        ],
        quick_links: [],
    };
    const RESULTS = {
        results: [
            { name: '.env', path: '/home/user/.env', size: 10, size_display: '10 B', extension: '', modified: '', directory: '/home/user' },
            { name: 'notes.txt', path: '/home/user/notes.txt', size: 10, size_display: '10 B', extension: '.txt', modified: '', directory: '/home/user' },
        ],
        total: 2,
        query: 'e',
        searched_locations: ['/home/user'],
        truncated: false,
    };

    const searchAndTick = async (names: string[], showHidden = false) => {
        mockedApi.browseFiles.mockResolvedValue(HOME);
        mockedApi.searchFiles.mockResolvedValue(RESULTS);
        render(<FileBrowser onStartChat={vi.fn()} />);
        await screen.findByText('Documents');
        if (showHidden) fireEvent.click(screen.getByRole('checkbox', { name: 'Show hidden' }));
        const input = screen.getByPlaceholderText('Search files on your PC...');
        fireEvent.change(input, { target: { value: 'e' } });
        fireEvent.keyDown(input, { key: 'Enter' });
        await screen.findByText(/Found 2 result/);
        for (const name of names) {
            fireEvent.click(screen.getByRole('checkbox', { name: `Select ${name}` }));
        }
    };

    it('keeps a hidden file ticked while its search results are on screen', async () => {
        await searchAndTick(['.env']);
        expect(screen.getByText('1 item(s) selected')).toBeInTheDocument();
    });

    it('drops a hidden file ticked in search on Back to browsing', async () => {
        await searchAndTick(['.env', 'notes.txt']);
        fireEvent.click(screen.getByRole('button', { name: 'Back to browsing' }));
        expect(await screen.findByText('1 item(s) selected')).toBeInTheDocument();
    });

    it('drops a hidden file ticked in search when the search is cleared', async () => {
        await searchAndTick(['.env']);
        fireEvent.click(screen.getByRole('button', { name: 'Clear search' }));
        await waitFor(() => expect(screen.getByRole('button', { name: /Index Selected/i })).toBeDisabled());
    });

    it('drops a hidden file with a Windows path on Back to browsing', async () => {
        mockedApi.browseFiles.mockResolvedValue(HOME);
        mockedApi.searchFiles.mockResolvedValue({
            ...RESULTS,
            results: [{ ...RESULTS.results[0], path: 'C:' + String.fromCharCode(92) + 'Users' + String.fromCharCode(92) + '.env' }],
            total: 1,
        });
        render(<FileBrowser onStartChat={vi.fn()} />);
        await screen.findByText('Documents');
        const input = screen.getByPlaceholderText('Search files on your PC...');
        fireEvent.change(input, { target: { value: 'e' } });
        fireEvent.keyDown(input, { key: 'Enter' });
        await screen.findByText(/Found 1 result/);
        fireEvent.click(screen.getByRole('checkbox', { name: 'Select .env' }));
        fireEvent.click(screen.getByRole('button', { name: 'Back to browsing' }));
        await waitFor(() => expect(screen.getByRole('button', { name: /Index Selected/i })).toBeDisabled());
    });

    it('keeps a hidden file ticked in search when Show hidden is on', async () => {
        await searchAndTick(['.env'], true);
        fireEvent.click(screen.getByRole('button', { name: 'Back to browsing' }));
        expect(await screen.findByText('1 item(s) selected')).toBeInTheDocument();
    });
});
