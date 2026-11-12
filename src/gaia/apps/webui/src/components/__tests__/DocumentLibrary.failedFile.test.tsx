// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentLibrary } from '../DocumentLibrary';
import { SessionDocsBar } from '../SessionDocsBar';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { Document } from '../../types';

vi.mock('../../services/api');
vi.mock('../../stores/modelNeededStore', () => ({ ensureModel: vi.fn(async () => true) }));

const mockedApi = vi.mocked(api);

const BROKEN: Document = {
    id: 'doc-broken',
    filename: 'broken.pdf',
    filepath: '/tmp/broken.pdf',
    file_size: 1024,
    chunk_count: 0,
    indexed_at: new Date().toISOString(),
    last_accessed_at: null,
    sessions_using: 0,
    indexing_status: 'failed',
    error_kind: 'corrupt',
    last_error: 'Could not read PDF: broken.pdf',
};

const GOOD: Document = {
    id: 'doc-good',
    filename: 'notes.txt',
    filepath: '/tmp/notes.txt',
    file_size: 2048,
    chunk_count: 3,
    indexed_at: new Date().toISOString(),
    last_accessed_at: null,
    sessions_using: 1,
    indexing_status: 'complete',
};

const chat = {
    id: 'sess-1', title: 'Budget review', created_at: '', updated_at: '', model: '',
    system_prompt: null, message_count: 1, document_ids: [] as string[],
};

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ currentSessionId: 'sess-1', sessions: [chat], showDocLibrary: true, draftDocumentIds: [], documents: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.attachDocument.mockResolvedValue(undefined);
});

function addByPath(path: string) {
    fireEvent.change(screen.getByLabelText(/^File path to/), { target: { value: path } });
    fireEvent.submit(screen.getByLabelText(/^File path to/).closest('form')!);
}

describe('a file GAIA could not read', () => {
    it('is not announced as attached, and is not attached', async () => {
        mockedApi.uploadDocumentByPath.mockResolvedValue(BROKEN);
        mockedApi.listDocuments
            .mockResolvedValueOnce({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 })
            .mockResolvedValue({ documents: [BROKEN], total: 1, total_size_bytes: 1024, total_chunks: 0 });
        render(<DocumentLibrary />);

        addByPath('/tmp/broken.pdf');

        expect(await screen.findByRole('alert')).toHaveTextContent("broken.pdf couldn't be added");
        expect(mockedApi.attachDocument).not.toHaveBeenCalled();
        expect(screen.queryByText(/Attached broken\.pdf/)).toBeNull();
    });

    it('is not held for the next new chat either', async () => {
        useChatStore.setState({ currentSessionId: null, sessions: [] });
        mockedApi.uploadDocumentByPath.mockResolvedValue(BROKEN);
        render(<DocumentLibrary />);

        addByPath('/tmp/broken.pdf');

        expect(await screen.findByRole('alert')).toHaveTextContent("broken.pdf couldn't be added");
        expect(useChatStore.getState().draftDocumentIds).toEqual([]);
    });

    it('is counted apart from the documents GAIA can read', async () => {
        mockedApi.listDocuments.mockResolvedValue({ documents: [BROKEN, GOOD], total: 2, total_size_bytes: 3072, total_chunks: 3 });
        render(<DocumentLibrary />);

        await screen.findByText('notes.txt');
        const stats = document.querySelector('.doc-stats')!;
        expect(stats).toHaveTextContent('1 document');
        expect(stats).toHaveTextContent("1 couldn't be read");
        expect(stats).not.toHaveTextContent('2 documents');
    });
});

describe('the chat bar with a document that failed', () => {
    it('counts only readable documents and marks the failed one', () => {
        useChatStore.setState({
            sessions: [{ ...chat, document_ids: ['doc-broken', 'doc-good'] }],
            documents: [BROKEN, GOOD],
        });
        render(<SessionDocsBar sessionId="sess-1" onDetachFailed={() => {}} />);

        expect(screen.getByRole('button', { name: "1 indexed · 1 couldn't be read" })).toBeInTheDocument();
        const failedPill = screen.getByText('broken.pdf').closest('.doc-pill')!;
        expect(failedPill).toHaveClass('doc-pill-failed');
        expect(failedPill.getAttribute('title')).toMatch(/couldn't be read/);
    });

    it('shows nothing green when the only document failed', async () => {
        useChatStore.setState({
            sessions: [{ ...chat, document_ids: ['doc-broken'] }],
            documents: [BROKEN],
        });
        render(<SessionDocsBar sessionId="sess-1" onDetachFailed={() => {}} />);

        await waitFor(() => expect(screen.getByRole('button', { name: "1 couldn't be read" })).toBeInTheDocument());
        expect(document.querySelector('.doc-context-icon.lucide-circle-check')).toBeNull();
    });
});
