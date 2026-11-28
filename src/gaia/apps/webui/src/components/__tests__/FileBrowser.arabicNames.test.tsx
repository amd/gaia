// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { FileBrowser } from '../FileBrowser';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import { resetUiLocale, setUiLocale } from '../../test/dates';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);
const LONG = 'Quarterly-planning-meeting-notes-for-the-northwest-regional-garden-cooperative-final-draft.md';

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.browseFiles.mockResolvedValue({
        current_path: '/home/user/docs',
        parent_path: '/home/user',
        entries: [{ name: LONG, path: `/home/user/docs/${LONG}`, type: 'file', size: 2_900_000, extension: '.md', modified: '' }],
        quick_links: [],
    });
    mockedApi.previewFile.mockResolvedValue({
        path: `/home/user/docs/${LONG}`,
        name: LONG,
        size: 2_900_000,
        size_display: '2.8 MB',
        extension: '.md',
        modified: '',
        is_text: true,
        preview_lines: ['notes'],
        total_lines: 1,
        columns: null,
        row_count: null,
    });
    useChatStore.setState({ currentSessionId: 'session-1', sessions: [], showFileBrowser: true });
    setUiLocale('ar');
});

afterEach(resetUiLocale);

it('keeps a file size left to right and lets a long English name pick its own direction in Arabic', async () => {
    render(<FileBrowser onStartChat={vi.fn()} />);
    const row = (await screen.findByText(LONG)).closest('.fb-entry') as HTMLElement;

    const size = row.querySelector('.fb-entry-size bdi');
    expect(size).toHaveTextContent('2.8 MB');
    expect(size).toHaveAttribute('dir', 'ltr');
    expect(row.querySelector('.fb-entry-name')).toHaveAttribute('dir', 'auto');

    fireEvent.click(row.querySelector('.fb-entry-preview-btn')!);
    await waitFor(() => expect(document.querySelector('.fb-preview-name')).not.toBeNull());
    expect(document.querySelector('.fb-preview-name')).toHaveAttribute('dir', 'auto');
});
