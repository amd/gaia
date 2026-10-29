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

describe('DocumentLibrary failure reasons', () => {
    it('shows why a document failed as visible text, not only a tooltip', async () => {
        render(<DocumentLibrary />);

        await screen.findByText('broken.pdf');

        expect(screen.getByText('RAG pipeline timed out', { selector: 'p' })).toBeTruthy();
        expect(screen.getByText('Failed')).toBeTruthy();
    });

    it('says a corrupt file needs re-exporting and keeps the full reason expandable', async () => {
        const corrupt: Document = {
            ...FAILED_DOC,
            error_kind: 'corrupted',
            last_error: 'RAG indexing failed for broken.pdf: Could not read PDF: broken.pdf\n'
                + 'Reason: Stream has ended unexpectedly\n'
                + 'The file appears to be corrupted, truncated, or not a valid PDF.',
        };
        mockedApi.listDocuments.mockResolvedValue({ documents: [corrupt], total: 1, total_size_bytes: 1024, total_chunks: 0 });
        render(<DocumentLibrary />);

        await screen.findByText('broken.pdf');

        expect(screen.getByText("Can't be read")).toBeTruthy();
        expect(screen.getByText(/looks damaged\. Re-download or re-export it/)).toBeTruthy();
        expect(screen.getByText('Could not read PDF: broken.pdf', { selector: 'summary' })).toBeTruthy();
        expect(screen.getByText(/Stream has ended unexpectedly/)).toBeTruthy();
        const retry = screen.getByRole('button', { name: /Retry indexing broken\.pdf/i });
        expect(retry.className).toContain('btn-retry-quiet');
    });

    it('says an empty file needs content instead of offering a plain Retry', async () => {
        const empty: Document = {
            ...FAILED_DOC,
            filename: 'empty.txt',
            file_size: 0,
            error_kind: 'empty_file',
            last_error: 'RAG indexing failed for empty.txt: File is empty',
        };
        mockedApi.listDocuments.mockResolvedValue({ documents: [empty], total: 1, total_size_bytes: 0, total_chunks: 0 });
        render(<DocumentLibrary />);

        await screen.findByText('empty.txt');

        expect(screen.getByText("Can't be read")).toBeTruthy();
        expect(screen.getByText('This file is empty. Add content to it, then index it again.')).toBeTruthy();
        const retry = screen.getByRole('button', { name: /Retry indexing empty\.txt/i });
        expect(retry.className).toContain('btn-retry-quiet');
    });

    it('says a dropped damaged PDF can\'t be read, with no bug-report link', async () => {
        mockedApi.uploadDocumentBlob.mockRejectedValue(Object.assign(
            new Error('RAG indexing failed for 1a2b_broken.pdf: Could not read PDF'),
            { httpStatus: 422, reason: 'corrupted' },
        ));
        const { container } = render(<DocumentLibrary />);
        await screen.findByText('broken.pdf');

        const dropped = new File(['%PDF-1.4 truncated'], 'report.pdf', { type: 'application/pdf' });
        fireEvent.drop(container.querySelector('.drop-zone')!, { dataTransfer: { files: [dropped] } });

        expect(await screen.findByText('"report.pdf" can\'t be read')).toBeTruthy();
        expect(screen.getByRole('alert').textContent).toContain('looks damaged. Re-download or re-export it');
        expect(screen.queryByText(/Report this issue/)).toBeNull();
    });
});

describe('DocumentLibrary path box', () => {
    const emptyList = { documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 };

    function submitPath(path: string) {
        fireEvent.change(screen.getByLabelText('File path to index'), { target: { value: path } });
        fireEvent.click(screen.getByRole('button', { name: 'Index' }));
    }

    it('indexes a folder path and says how many files it added and skipped', async () => {
        mockedApi.listDocuments.mockResolvedValue(emptyList);
        mockedApi.uploadDocumentByPath.mockRejectedValue(
            Object.assign(new Error('C:\\Users\\me\\.gaia\\documents is a folder, not a file.'), {
                httpStatus: 400, reason: 'is_folder',
            }),
        );
        mockedApi.indexFolder.mockResolvedValue({
            indexed: 2, failed: 0, skipped: 1, errors: [],
            documents: [COMPLETE_DOC, { ...COMPLETE_DOC, id: 'doc-ok-2', filename: 'b.md' }],
        });
        render(<DocumentLibrary />);

        submitPath('C:\\Users\\me\\.gaia\\documents');

        const notice = await screen.findByRole('status');
        expect(notice.textContent).toContain('Indexed 2 files from "documents".');
        expect(notice.textContent).toContain("Skipped 1 file of a type GAIA doesn't index.");
        expect(mockedApi.indexFolder).toHaveBeenCalledWith('C:\\Users\\me\\.gaia\\documents', false);
        expect(useChatStore.getState().draftDocumentIds).toEqual(['doc-ok-1', 'doc-ok-2']);
        expect(screen.queryByRole('alert')).toBeNull();
    });

    it('reports a folder with nothing indexable plainly', async () => {
        mockedApi.listDocuments.mockResolvedValue(emptyList);
        mockedApi.uploadDocumentByPath.mockRejectedValue(
            Object.assign(new Error('is a folder'), { httpStatus: 400, reason: 'is_folder' }),
        );
        mockedApi.indexFolder.mockResolvedValue({ indexed: 0, failed: 0, skipped: 3, errors: [], documents: [] });
        render(<DocumentLibrary />);

        submitPath('/home/me/pictures');

        const notice = await screen.findByRole('status');
        expect(notice.textContent).toContain('"pictures" has no documents GAIA can index.');
        expect(notice.textContent).toContain('Skipped 3 files');
    });

    it('says a missing file does not exist instead of offering a bug report', async () => {
        mockedApi.listDocuments.mockResolvedValue(emptyList);
        mockedApi.uploadDocumentByPath.mockRejectedValue(
            Object.assign(new Error('File not found: there is no file at C:\\Users\\me\\missing.pdf. Check the path for typos.'), {
                httpStatus: 404, reason: null,
            }),
        );
        render(<DocumentLibrary />);

        submitPath('C:\\Users\\me\\missing.pdf');

        const toast = await screen.findByRole('alert');
        expect(toast.textContent).toContain(`"missing.pdf" doesn't exist`);
        expect(toast.textContent).toContain('Check the path');
        expect(toast.textContent).not.toContain('Report this issue');
        expect(mockedApi.indexFolder).not.toHaveBeenCalled();
    });

    it('indexes a dotted folder name typed with a trailing separator', async () => {
        mockedApi.listDocuments.mockResolvedValue(emptyList);
        mockedApi.uploadDocumentByPath.mockRejectedValue(
            Object.assign(new Error('is a folder'), { httpStatus: 400, reason: 'is_folder' }),
        );
        mockedApi.indexFolder.mockResolvedValue({
            indexed: 1, failed: 0, skipped: 0, errors: [], documents: [COMPLETE_DOC],
        });
        render(<DocumentLibrary />);

        submitPath('C:\\Users\\me\\notes.v2\\');

        const notice = await screen.findByRole('status');
        expect(notice.textContent).toContain('Indexed 1 file from "notes.v2".');
        expect(mockedApi.uploadDocumentByPath).toHaveBeenCalledWith('C:\\Users\\me\\notes.v2\\');
        expect(screen.queryByRole('alert')).toBeNull();
    });

    it('asks the server about an unsupported typed path, then names the file type', async () => {
        mockedApi.listDocuments.mockResolvedValue(emptyList);
        mockedApi.uploadDocumentByPath.mockRejectedValue(
            Object.assign(new Error("File type not allowed: GAIA can't index .png files."), {
                httpStatus: 400, reason: null,
            }),
        );
        render(<DocumentLibrary />);

        submitPath('C:\\Users\\me\\photo.png');

        const toast = await screen.findByRole('alert');
        expect(toast.textContent).toContain('Image files cannot be indexed for text search.');
        expect(mockedApi.uploadDocumentByPath).toHaveBeenCalledWith('C:\\Users\\me\\photo.png');
        expect(mockedApi.indexFolder).not.toHaveBeenCalled();
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
