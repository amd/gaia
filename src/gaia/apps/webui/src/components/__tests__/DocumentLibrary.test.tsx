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
        draftDocumentIds: [],
    });
});

it('closes on Escape', async () => {
    render(<DocumentLibrary />);
    await screen.findByText('broken.pdf');

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(useChatStore.getState().showDocLibrary).toBe(false);
});

describe('DocumentLibrary with no chat open', () => {
    it('holds a newly indexed document for the next chat instead of dropping it', async () => {
        mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
        mockedApi.uploadDocumentByPath.mockResolvedValue(COMPLETE_DOC);
        render(<DocumentLibrary />);

        fireEvent.change(screen.getByLabelText('File path to index'), { target: { value: '/tmp/good.pdf' } });
        fireEvent.click(screen.getByRole('button', { name: 'Index' }));

        await waitFor(() => expect(useChatStore.getState().draftDocumentIds).toEqual(['doc-ok-1']));
        expect(mockedApi.attachDocument).not.toHaveBeenCalled();
    });

    it('offers an existing document for the next chat, once', async () => {
        mockedApi.listDocuments.mockResolvedValue({ documents: [COMPLETE_DOC], total: 1, total_size_bytes: 2048, total_chunks: 5 });
        render(<DocumentLibrary />);

        fireEvent.click(await screen.findByRole('button', { name: 'Attach good.pdf to your next chat' }));

        expect(useChatStore.getState().draftDocumentIds).toEqual(['doc-ok-1']);
        expect(screen.queryByRole('button', { name: 'Attach good.pdf to your next chat' })).toBeNull();
        expect(mockedApi.attachDocument).not.toHaveBeenCalled();
    });

    it('forgets a held document that is removed from the library', async () => {
        mockedApi.listDocuments.mockResolvedValue({ documents: [COMPLETE_DOC], total: 1, total_size_bytes: 2048, total_chunks: 5 });
        mockedApi.deleteDocument.mockResolvedValue(undefined as never);
        useChatStore.setState({ draftDocumentIds: ['doc-ok-1'] });
        render(<DocumentLibrary />);

        fireEvent.click(await screen.findByRole('button', { name: 'Remove good.pdf' }));

        await waitFor(() => expect(useChatStore.getState().draftDocumentIds).toEqual([]));
    });

    it('still attaches straight to the open chat when there is one', async () => {
        const chat = {
            id: 'sess-1', title: 'Chat', created_at: '', updated_at: '', model: '',
            system_prompt: null, message_count: 1, document_ids: [],
        };
        useChatStore.setState({ currentSessionId: 'sess-1', sessions: [chat] });
        mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
        mockedApi.uploadDocumentByPath.mockResolvedValue(COMPLETE_DOC);
        mockedApi.attachDocument.mockResolvedValue(undefined);
        render(<DocumentLibrary />);

        fireEvent.change(screen.getByLabelText('File path to index'), { target: { value: '/tmp/good.pdf' } });
        fireEvent.click(screen.getByRole('button', { name: 'Index' }));

        await waitFor(() => expect(mockedApi.attachDocument).toHaveBeenCalledWith('sess-1', 'doc-ok-1'));
        expect(useChatStore.getState().draftDocumentIds).toEqual([]);
    });
});

describe('DocumentLibrary says where documents go', () => {
    const chat = {
        id: 'sess-1', title: 'Budget review', created_at: '', updated_at: '', model: '',
        system_prompt: null, message_count: 1, document_ids: [],
    };

    beforeEach(() => {
        useChatStore.setState({ currentSessionId: 'sess-1', sessions: [chat] });
        mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
        mockedApi.uploadDocumentByPath.mockResolvedValue(COMPLETE_DOC);
        mockedApi.attachDocument.mockResolvedValue(undefined);
    });

    const indexPath = () => {
        fireEvent.change(screen.getByLabelText('File path to index'), { target: { value: '/tmp/good.pdf' } });
        fireEvent.click(screen.getByRole('button', { name: 'Index' }));
    };

    it('names the open chat in the attach button label, for screen readers too', async () => {
        mockedApi.listDocuments.mockResolvedValue({ documents: [COMPLETE_DOC], total: 1, total_size_bytes: 2048, total_chunks: 5 });
        render(<DocumentLibrary />);

        const button = await screen.findByRole('button', { name: 'Attach good.pdf to “Budget review”' });
        expect(button.getAttribute('title')).toBe('Use in “Budget review”');
    });

    it('names the open chat as the destination and confirms the attach', async () => {
        render(<DocumentLibrary />);

        const choice = screen.getByRole('combobox', { name: 'Add documents to' });
        expect(choice).toHaveValue('chat');
        expect(screen.getByRole('option', { name: 'This chat: “Budget review”' })).toBeInTheDocument();
        indexPath();

        expect(await screen.findByRole('status')).toHaveTextContent('Attached good.pdf to “Budget review”.');
    });

    it('holds documents for the next new chat when chosen, leaving the open chat alone', async () => {
        render(<DocumentLibrary />);

        fireEvent.change(screen.getByRole('combobox', { name: 'Add documents to' }), { target: { value: 'next' } });
        indexPath();

        await waitFor(() => expect(useChatStore.getState().draftDocumentIds).toEqual(['doc-ok-1']));
        expect(mockedApi.attachDocument).not.toHaveBeenCalled();
        expect(screen.getByRole('status')).toHaveTextContent('good.pdf will be attached to your next new chat.');
    });

    it('says so when indexing worked but attaching did not', async () => {
        mockedApi.attachDocument.mockRejectedValue(new Error('Session not found'));
        render(<DocumentLibrary />);

        indexPath();

        expect(await screen.findByRole('alert')).toHaveTextContent('Indexed, but not attached to “Budget review”: Session not found');
    });

    it('with no chat open, says documents go to the next new chat', () => {
        useChatStore.setState({ currentSessionId: null });
        render(<DocumentLibrary />);

        expect(screen.getByText('Documents you add here are attached to your next new chat.')).toBeInTheDocument();
        expect(screen.queryByRole('combobox', { name: 'Add documents to' })).toBeNull();
    });

    it('explains a file outside the readable folders without asking for a bug report', async () => {
        mockedApi.uploadDocumentByPath.mockRejectedValue(Object.assign(
            new Error('GAIA can only read documents under C:\\Users\\u. Move or copy the file there, or add its folder to GAIA_DOCUMENT_ROOTS and restart GAIA.'),
            { httpStatus: 403 },
        ));
        render(<DocumentLibrary />);

        indexPath();

        const toast = await screen.findByRole('alert');
        expect(toast).toHaveTextContent('Move or copy the file there');
        expect(toast).toHaveTextContent('Or drop the file here instead');
        expect(screen.queryByRole('link', { name: /report this issue/i })).toBeNull();
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

describe('DocumentLibrary chunk counts', () => {
    it('says "1 chunk", not "1 chunks"', async () => {
        mockedApi.listDocuments.mockResolvedValue({
            documents: [{ ...COMPLETE_DOC, chunk_count: 1 }],
            total: 1,
            total_size_bytes: 2048,
            total_chunks: 1,
        });
        render(<DocumentLibrary />);

        await screen.findByText('good.pdf');

        expect(screen.getByText('1 chunk')).toBeTruthy();
        expect(screen.getByText(/· 1 chunk$/)).toBeTruthy();
        expect(screen.queryByText(/1 chunks/)).toBeNull();
    });

    it('keeps the plural for other counts', async () => {
        mockedApi.listDocuments.mockResolvedValue({
            documents: [COMPLETE_DOC],
            total: 1,
            total_size_bytes: 2048,
            total_chunks: 5,
        });
        render(<DocumentLibrary />);

        await screen.findByText('good.pdf');

        expect(screen.getByText('5 chunks')).toBeTruthy();
        expect(screen.getByText(/· 5 chunks$/)).toBeTruthy();
    });
});
