// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FileBrowser } from '../FileBrowser';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import { resetUiLocale, setUiLocale } from '../../test/dates';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const EMPTY_FILE_PREVIEW = {
    path: '/home/user/docs/empty.txt',
    name: 'empty.txt',
    size: 0,
    size_display: '0.0 B',
    extension: '.txt',
    modified: '',
    is_text: true,
    preview_lines: [],
    total_lines: 0,
    columns: null,
    row_count: null,
};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.browseFiles.mockResolvedValue({
        current_path: '/home/user/docs',
        parent_path: '/home/user',
        entries: [
            { name: 'empty.txt', path: '/home/user/docs/empty.txt', type: 'file', size: 0, extension: '.txt', modified: '' },
        ],
        quick_links: [],
    });
    mockedApi.previewFile.mockResolvedValue(EMPTY_FILE_PREVIEW);
    useChatStore.setState({ currentSessionId: 'session-1', sessions: [], showFileBrowser: true });
});

afterEach(resetUiLocale);

async function openPreview(): Promise<HTMLElement> {
    render(<FileBrowser onStartChat={vi.fn()} />);
    const row = (await screen.findByText('empty.txt')).closest('.fb-entry') as HTMLElement;
    fireEvent.click(row.querySelector('.fb-entry-preview-btn')!);
    await waitFor(() => expect(document.querySelector('.fb-preview-binary')).not.toBeNull());
    return document.querySelector('.fb-preview') as HTMLElement;
}

describe('FileBrowser preview of an empty text file', () => {
    it('says the file is empty, with only the size above it and no binary-file message', async () => {
        const preview = await openPreview();
        expect(preview.querySelector('.fb-preview-binary')).toHaveTextContent('This file is empty');
        expect(preview.querySelector('.fb-preview-meta')!.children).toHaveLength(1);
        expect(preview).not.toHaveTextContent('Binary file');
    });

    it('says it in Arabic in an Arabic UI', async () => {
        setUiLocale('ar');
        const preview = await openPreview();
        expect(preview.querySelector('.fb-preview-binary')).toHaveTextContent('هذا الملف فارغ');
    });

    it('still calls a non-text file binary', async () => {
        mockedApi.previewFile.mockResolvedValue({ ...EMPTY_FILE_PREVIEW, is_text: false, total_lines: null });
        render(<FileBrowser onStartChat={vi.fn()} />);
        const row = (await screen.findByText('empty.txt')).closest('.fb-entry') as HTMLElement;
        fireEvent.click(row.querySelector('.fb-entry-preview-btn')!);
        expect(await screen.findByText('Binary file - no preview available')).toBeInTheDocument();
    });
});
