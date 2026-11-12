// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, fireEvent, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentLibrary } from '../DocumentLibrary';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { Document } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

function doc(id: string, filename: string, sessions_using: number): Document {
    return {
        id, filename, filepath: `/home/me/${filename}`, file_size: 2048, chunk_count: 4,
        indexed_at: new Date().toISOString(), last_accessed_at: null, sessions_using,
        indexing_status: 'complete',
    };
}

const HERE = doc('d1', 'budget.xlsx', 2);
const ELSEWHERE = doc('d2', 'lease.pdf', 3);
const NOWHERE = doc('d3', 'garden-notes.txt', 0);

const chat = {
    id: 'sess-1', title: 'Budget review', created_at: '', updated_at: '', model: '',
    system_prompt: null, message_count: 1, document_ids: ['d1'],
};

function listing(documents: Document[]) {
    return { documents, total: documents.length, total_size_bytes: 2048 * documents.length, total_chunks: 4 };
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.listDocuments.mockResolvedValue(listing([HERE, ELSEWHERE, NOWHERE]));
    useChatStore.setState({ currentSessionId: 'sess-1', sessions: [chat], documents: [], showDocLibrary: true, draftDocumentIds: [] });
});

function row(name: string): HTMLElement {
    return screen.getByText(name).closest('.doc-row') as HTMLElement;
}

describe('the library says where each document is used', () => {
    it('marks the open chat, other chats and no chat', async () => {
        render(<DocumentLibrary />);
        await screen.findByText('budget.xlsx');

        expect(within(row('budget.xlsx')).getByText('In this chat and 1 other')).toBeInTheDocument();
        expect(within(row('lease.pdf')).getByText('In 3 chats')).toBeInTheDocument();
        expect(within(row('garden-notes.txt')).getByText('Not in any chat')).toBeInTheDocument();
    });

    it('says "1 chat", not "1 chats"', async () => {
        mockedApi.listDocuments.mockResolvedValue(listing([doc('d4', 'one.pdf', 1)]));
        render(<DocumentLibrary />);

        expect(await screen.findByText('In 1 chat')).toBeInTheDocument();
    });

    it('offers a labelled "Add to this chat" on a document the chat does not have', async () => {
        render(<DocumentLibrary />);
        await screen.findByText('budget.xlsx');

        expect(within(row('lease.pdf')).getByRole('button', { name: 'Attach lease.pdf to “Budget review”' })).toHaveTextContent('Add to this chat');
        expect(within(row('budget.xlsx')).queryByText('Add to this chat')).toBeNull();
    });
});

describe('the library filter', () => {
    it('narrows the list by name once there are enough documents to need it', async () => {
        const many = Array.from({ length: 7 }, (_, i) => doc(`m${i}`, `report-${i}.pdf`, 0));
        mockedApi.listDocuments.mockResolvedValue(listing([...many, NOWHERE]));
        render(<DocumentLibrary />);
        await screen.findByText('garden-notes.txt');

        fireEvent.change(screen.getByRole('searchbox', { name: 'Filter documents' }), { target: { value: 'GARDEN' } });

        expect(screen.getByText('garden-notes.txt')).toBeVisible();
        expect(screen.getByText('report-0.pdf')).not.toBeVisible();
    });

    it('says so when nothing matches', async () => {
        const many = Array.from({ length: 7 }, (_, i) => doc(`m${i}`, `report-${i}.pdf`, 0));
        mockedApi.listDocuments.mockResolvedValue(listing(many));
        render(<DocumentLibrary />);
        await screen.findByText('report-0.pdf');

        fireEvent.change(screen.getByRole('searchbox', { name: 'Filter documents' }), { target: { value: 'zzz' } });

        expect(screen.getByText('No documents match “zzz”.')).toBeInTheDocument();
    });

    it('is not shown for a short list', async () => {
        render(<DocumentLibrary />);
        await screen.findByText('budget.xlsx');

        expect(screen.queryByRole('searchbox', { name: 'Filter documents' })).toBeNull();
    });
});
