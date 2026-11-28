// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { FileBrowser } from '../FileBrowser';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.browseFiles.mockResolvedValue({
        current_path: '/home/user/docs',
        parent_path: '/home/user',
        entries: [
            { name: 'fruit.csv', path: '/home/user/docs/fruit.csv', type: 'file', size: 24, extension: '.csv', modified: '' },
        ],
        quick_links: [],
    });
    // The server's own size text always carries a decimal ("24.0 B").
    mockedApi.previewFile.mockResolvedValue({
        path: '/home/user/docs/fruit.csv',
        name: 'fruit.csv',
        size: 24,
        size_display: '24.0 B',
        extension: '.csv',
        modified: '',
        is_text: true,
        preview_lines: ['name,qty'],
        total_lines: 1,
        columns: null,
        row_count: null,
    });
    useChatStore.setState({ currentSessionId: 'session-1', sessions: [], showFileBrowser: true });
});

it('shows a file the same size in its preview as in the list', async () => {
    render(<FileBrowser onStartChat={vi.fn()} />);
    const row = (await screen.findByText('fruit.csv')).closest('.fb-entry') as HTMLElement;
    const listed = row.querySelector('.fb-entry-size')!.textContent;
    fireEvent.click(row.querySelector('.fb-entry-preview-btn')!);
    await waitFor(() => expect(document.querySelector('.fb-preview-meta')).not.toBeNull());
    const previewed = document.querySelector('.fb-preview-meta span')!.textContent;
    expect(listed).toBe('24 B');
    expect(previewed).toBe(listed);
});
