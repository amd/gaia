// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Removing a document, cancelling its indexing and opening its folder used to
 * fail with only a console line, and a library that couldn't be listed showed
 * as empty, as if every document were gone (#4817).
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentLibrary } from '../DocumentLibrary';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { Document } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

function doc(id: string, filename: string, status: Document['indexing_status'] = 'complete'): Document {
    return {
        id, filename, filepath: `/home/me/${filename}`, file_size: 1024, chunk_count: 3,
        indexed_at: new Date().toISOString(), last_accessed_at: null, sessions_using: 0,
        indexing_status: status,
    };
}

const REPORT = doc('d1', 'report.pdf');
const NOTES = doc('d2', 'notes.md');

function listing(documents: Document[]) {
    return { documents, total: documents.length, total_size_bytes: 1024 * documents.length, total_chunks: 3 };
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.listDocuments.mockResolvedValue(listing([REPORT]));
    useChatStore.setState({ currentSessionId: null, sessions: [], documents: [], showDocLibrary: true, draftDocumentIds: [] });
});

afterEach(() => {
    vi.useRealTimers();
});

describe('Document Library actions that fail say so', () => {
    it('a remove the backend refuses names the document and keeps it listed', async () => {
        mockedApi.deleteDocument.mockRejectedValue(new Error('database is locked'));
        render(<DocumentLibrary />);
        await screen.findByText('report.pdf');

        fireEvent.click(screen.getByRole('button', { name: 'Remove report.pdf' }));

        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't remove report.pdf: database is locked");
        expect(screen.getByText('report.pdf')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Dismiss error' }));
        await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    });

    it('a cancel that fails says so', async () => {
        mockedApi.listDocuments.mockResolvedValue(listing([doc('d3', 'big.pdf', 'indexing')]));
        mockedApi.getDocumentStatus.mockReturnValue(new Promise(() => {}));
        mockedApi.cancelIndexing.mockRejectedValue(new Error('no such job'));
        render(<DocumentLibrary />);
        await screen.findByText('big.pdf');

        fireEvent.click(screen.getByRole('button', { name: 'Cancel indexing big.pdf' }));

        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't cancel indexing big.pdf: no such job");
    });

    it('a cancel that works is not reported as failed when the refresh after it fails', async () => {
        mockedApi.listDocuments
            .mockResolvedValueOnce(listing([doc('d3', 'big.pdf', 'indexing')]))
            .mockRejectedValueOnce(new Error('timeout'));
        mockedApi.getDocumentStatus.mockReturnValue(new Promise(() => {}));
        mockedApi.cancelIndexing.mockResolvedValue({} as never);
        render(<DocumentLibrary />);
        await screen.findByText('big.pdf');

        fireEvent.click(screen.getByRole('button', { name: 'Cancel indexing big.pdf' }));

        await waitFor(() => expect(mockedApi.listDocuments).toHaveBeenCalledTimes(2));
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('a folder that will not open says so', async () => {
        mockedApi.openFileOrFolder.mockRejectedValue(new Error('path not allowed'));
        render(<DocumentLibrary />);
        await screen.findByText('report.pdf');

        fireEvent.click(screen.getByRole('button', { name: 'Open containing folder for report.pdf' }));

        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't open the folder for report.pdf: path not allowed");
    });

    it('a library that cannot be listed says so instead of looking empty, and Try again reloads it', async () => {
        mockedApi.listDocuments.mockRejectedValueOnce(new Error('backend is starting'));
        render(<DocumentLibrary />);

        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load your documents: backend is starting");
        expect(screen.queryByText('No documents yet')).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

        expect(await screen.findByText('report.pdf')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('a load that fails over documents already listed keeps them and says so in a notice', async () => {
        useChatStore.setState({ documents: [NOTES] });
        mockedApi.listDocuments.mockRejectedValueOnce(new Error('backend is starting'));
        const { container } = render(<DocumentLibrary />);

        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load your documents: backend is starting");
        expect(screen.getByText('notes.md')).toBeInTheDocument();
        // The big empty-state error would read as if the listed documents were gone.
        expect(container.querySelector('.doc-load-error')).toBeNull();

        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

        expect(await screen.findByText('report.pdf')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('a remove keeps documents a refresh added while it was in flight', async () => {
        let finishDelete: () => void = () => {};
        mockedApi.deleteDocument.mockReturnValue(new Promise<void>((resolve) => { finishDelete = resolve; }));
        render(<DocumentLibrary />);
        await screen.findByText('report.pdf');

        fireEvent.click(screen.getByRole('button', { name: 'Remove report.pdf' }));
        act(() => useChatStore.getState().setDocuments([REPORT, NOTES]));
        await act(async () => finishDelete());

        expect(useChatStore.getState().documents.map((d) => d.id)).toEqual([NOTES.id]);
    });
});
