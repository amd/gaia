// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentLibrary, NAME_TAIL_CHARS } from '../DocumentLibrary';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { Document } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const LONG = 'Quarterly business review for the regional sales team final draft v7 (reviewed by finance).pdf';

function doc(filename: string): Document {
    return {
        id: filename, filename, filepath: `/home/me/${filename}`, file_size: 2048, chunk_count: 3,
        indexed_at: new Date().toISOString(), last_accessed_at: null, sessions_using: 0, indexing_status: 'complete',
    };
}

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ currentSessionId: null, sessions: [], documents: [], showDocLibrary: true, draftDocumentIds: [] });
});

describe('long document names', () => {
    it('keep their end, so the extension and version stay readable', async () => {
        mockedApi.listDocuments.mockResolvedValue({ documents: [doc(LONG)], total: 1, total_size_bytes: 2048, total_chunks: 3 });
        render(<DocumentLibrary />);

        const name = await screen.findByTitle(LONG);
        const tail = name.querySelector('.doc-name-tail')!;
        expect(tail.textContent).toBe(LONG.slice(-NAME_TAIL_CHARS));
        expect(tail.textContent).toMatch(/finance\)\.pdf$/);
        expect(name.textContent).toBe(LONG);
    });

    it('never cut an emoji in half', async () => {
        // The emoji sits right where a cut by UTF-16 units would split its surrogate pair.
        const name = `${'a'.repeat(40)}\u{1F4C4}${'b'.repeat(NAME_TAIL_CHARS - 1)}`;
        mockedApi.listDocuments.mockResolvedValue({ documents: [doc(name)], total: 1, total_size_bytes: 2048, total_chunks: 3 });
        render(<DocumentLibrary />);

        const tail = (await screen.findByTitle(name)).querySelector('.doc-name-tail')!;
        expect(tail.textContent).toBe(`\u{1F4C4}${'b'.repeat(NAME_TAIL_CHARS - 1)}`);
    });

    it('follow the first letter for direction, whatever its script', async () => {
        const cyrillicFirst = `Отчёт за квартал итоговый вариант ${'تقرير'} final.pdf`;
        const arabicFirst = 'ﻣﺮﺍﺟﻌﺔ الأعمال الفصلية لفريق المبيعات الإقليمي.docx';
        mockedApi.listDocuments.mockResolvedValue({
            documents: [doc(cyrillicFirst), doc(arabicFirst)], total: 2, total_size_bytes: 4096, total_chunks: 6,
        });
        render(<DocumentLibrary />);

        expect((await screen.findByTitle(cyrillicFirst)).classList.contains('is-rtl-text')).toBe(false);
        expect((await screen.findByTitle(arabicFirst)).classList.contains('is-rtl-text')).toBe(true);
    });

    it('a short name stays one piece of text', async () => {
        mockedApi.listDocuments.mockResolvedValue({ documents: [doc('notes.txt')], total: 1, total_size_bytes: 2048, total_chunks: 3 });
        render(<DocumentLibrary />);

        expect((await screen.findByText('notes.txt')).querySelector('.doc-name-tail')).toBeNull();
    });
});

it('says before a folder is added that its subfolders are skipped', async () => {
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    render(<DocumentLibrary />);

    expect(await screen.findByText(/A folder typed here adds only .* subfolders are skipped/)).toBeInTheDocument();
});
