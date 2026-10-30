// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IndexingProgress } from '../IndexingProgress';
import { DocumentLibrary } from '../DocumentLibrary';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { Document } from '../../types';
import { stripIsolates } from '../../i18n/registry';

vi.mock('../../services/api');
const mockedApi = vi.mocked(api);

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
    localStorage.removeItem('gaia-chat-locale');
});

describe('IndexingProgress', () => {
    it('says which page is being read, with a percent', () => {
        render(<IndexingProgress progress={{ phase: 'reading', done: 37, total: 229 }} />);
        expect(screen.getByText('Reading page 37 of 229')).toBeInTheDocument();
        expect(screen.getByText('· 16%')).toBeInTheDocument();
        expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '16');
    });

    it('counts chunks embedded, with thousands separators', () => {
        render(<IndexingProgress progress={{ phase: 'embedding', done: 412, total: 1180 }} />);
        expect(screen.getByText('Embedding 412 of 1,180')).toBeInTheDocument();
        expect(screen.getByText('· 34%')).toBeInTheDocument();
    });

    it('shows just the count, and no percent, when the total is unknown', () => {
        render(<IndexingProgress progress={{ phase: 'reading', done: 12, total: null }} />);
        expect(screen.getByText('Reading page 12')).toBeInTheDocument();
        expect(screen.queryByText(/%/)).toBeNull();
        expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    });

    it('reads in Arabic with the English in brackets', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        render(<IndexingProgress progress={{ phase: 'reading', done: 37, total: 229 }} />);
        expect(screen.getByText((content) => stripIsolates(content) === 'قراءة الصفحة 37 من 229 (Reading page 37 of 229)'))
            .toBeInTheDocument();
    });
});

const INDEXING_DOC: Document = {
    id: 'doc-big-1',
    filename: 'annual-report.pdf',
    filepath: '/tmp/annual-report.pdf',
    file_size: 9_000_000,
    chunk_count: 0,
    indexed_at: new Date().toISOString(),
    last_accessed_at: null,
    sessions_using: 0,
    indexing_status: 'indexing',
};

describe('DocumentLibrary indexing progress', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mockedApi.listDocuments.mockResolvedValue({
            documents: [INDEXING_DOC], total: 1, total_size_bytes: INDEXING_DOC.file_size, total_chunks: 0,
        });
        mockedApi.getDocumentStatus.mockResolvedValue({
            id: INDEXING_DOC.id,
            indexing_status: 'indexing',
            chunk_count: 0,
            is_active: true,
            progress: { phase: 'reading', done: 37, total: 229 },
        });
        useChatStore.setState({ currentSessionId: null, sessions: [], showDocLibrary: true, draftDocumentIds: [] });
    });

    it('shows the polled progress on the indexing row', async () => {
        render(<DocumentLibrary />);
        expect(await screen.findByText('Reading page 37 of 229', undefined, { timeout: 3000 })).toBeInTheDocument();
        expect(mockedApi.getDocumentStatus).toHaveBeenCalledWith(INDEXING_DOC.id);
        expect(screen.getByRole('button', { name: /Cancel indexing annual-report\.pdf/ })).toBeInTheDocument();
    });
});
