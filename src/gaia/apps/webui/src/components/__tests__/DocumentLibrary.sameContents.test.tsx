// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A renamed copy of a library file comes back from the server as the file already there.
// The notice must name the file the user picked, not only the old one.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentLibrary } from '../DocumentLibrary';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { Document } from '../../types';
import { resetUiLocale, setUiLocale } from '../../test/dates';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const FRUIT: Document = {
    id: 'doc-fruit',
    filename: 'fruit.csv',
    filepath: '/tmp/fruit.csv',
    file_size: 37,
    chunk_count: 1,
    indexed_at: new Date().toISOString(),
    last_accessed_at: null,
    sessions_using: 0,
    indexing_status: 'complete',
};

const add = (path: string) => {
    fireEvent.change(document.querySelector('input[type="text"]')!, { target: { value: path } });
    fireEvent.submit(document.querySelector('form')!);
};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.listDocuments.mockResolvedValue({ documents: [FRUIT], total: 1, total_size_bytes: 37, total_chunks: 1 });
    mockedApi.uploadDocumentByPath.mockResolvedValue(FRUIT);
    useChatStore.setState({ currentSessionId: null, sessions: [], showDocLibrary: true, draftDocumentIds: [] });
});

afterEach(resetUiLocale);

describe('adding a copy of a file already in the library', () => {
    it('says the picked file has the same contents as the existing one', async () => {
        render(<DocumentLibrary />);
        await screen.findByText('fruit.csv');

        add('/tmp/pantry list 2.csv');

        await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(
            'pantry list 2.csv has the same contents as fruit.csv, which is already in your library.',
        ));
        expect(screen.getByRole('status')).toHaveTextContent('fruit.csv will be added to your next new chat.');
    });

    it('keeps the plain notice when the same file is added again', async () => {
        render(<DocumentLibrary />);
        await screen.findByText('fruit.csv');

        add('/tmp/fruit.csv');

        await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('fruit.csv will be added to your next new chat.'));
        expect(screen.getByRole('status')).not.toHaveTextContent('same contents');
    });

    it('says it in Arabic too', async () => {
        setUiLocale('ar');
        render(<DocumentLibrary />);
        await screen.findByText('fruit.csv');

        add('/tmp/pantry list 2.csv');

        await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/pantry list 2\.csv.*نفس محتوى.*fruit\.csv/s));
    });
});
