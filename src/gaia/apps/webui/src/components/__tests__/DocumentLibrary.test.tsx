// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentLibrary } from '../DocumentLibrary';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { Document } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const FAILED_DOC: Document = {
    id: 'doc-fail-1',
    filename: 'broken.pdf',
    filepath: '/tmp/broken.pdf',
    file_size: 1024,
    chunk_count: 0,
    indexed_at: new Date().toISOString(),
    last_accessed_at: null,
    sessions_using: 0,
    indexing_status: 'failed',
    last_error: 'RAG pipeline timed out',
};

const COMPLETE_DOC: Document = {
    id: 'doc-ok-1',
    filename: 'good.pdf',
    filepath: '/tmp/good.pdf',
    file_size: 2048,
    chunk_count: 5,
    indexed_at: new Date().toISOString(),
    last_accessed_at: null,
    sessions_using: 0,
    indexing_status: 'complete',
};

beforeEach(() => {
    vi.clearAllMocks();

    mockedApi.listDocuments.mockResolvedValue({
        documents: [FAILED_DOC],
        total: 1,
        total_size_bytes: 1024,
        total_chunks: 0,
    });
    mockedApi.reindexDocument.mockResolvedValue({ ...FAILED_DOC, indexing_status: 'complete', last_error: null, chunk_count: 3 });

    useChatStore.setState({
        currentSessionId: null,
        sessions: [],
        showDocLibrary: true,
    });
});

describe('DocumentLibrary failed doc', () => {
    it('renders a failed document with a Retry button', async () => {
        render(<DocumentLibrary />);

        // Wait for the document list to load
        await screen.findByText('broken.pdf');

        const retryBtn = screen.getByRole('button', { name: /Retry indexing broken\.pdf/i });
        expect(retryBtn).toBeTruthy();
    });

    it('shows the error message as the badge title attribute', async () => {
        render(<DocumentLibrary />);

        await screen.findByText('broken.pdf');

        // The Failed badge wraps an AlertCircle + "Failed" text and carries the error as title
        const badge = screen.getByTitle('RAG pipeline timed out');
        expect(badge).toBeTruthy();
    });

    it('clicking Retry calls reindexDocument and refreshes the list', async () => {
        mockedApi.listDocuments.mockResolvedValueOnce({
            documents: [FAILED_DOC],
            total: 1,
            total_size_bytes: 1024,
            total_chunks: 0,
        }).mockResolvedValueOnce({
            documents: [{ ...FAILED_DOC, indexing_status: 'complete', last_error: null, chunk_count: 3 }],
            total: 1,
            total_size_bytes: 1024,
            total_chunks: 3,
        });

        render(<DocumentLibrary />);

        await screen.findByText('broken.pdf');

        const retryBtn = screen.getByRole('button', { name: /Retry indexing broken\.pdf/i });
        fireEvent.click(retryBtn);

        await waitFor(() => {
            expect(mockedApi.reindexDocument).toHaveBeenCalledWith('doc-fail-1');
        });

        await waitFor(() => {
            expect(mockedApi.listDocuments).toHaveBeenCalledTimes(2);
        });
    });
});
