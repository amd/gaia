// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FileBrowser } from '../FileBrowser';
import type { BrowseResponse } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

// The component destructures a few setters/values from the store and uses
// useChatStore.getState() inside the index path (not exercised here).
const storeState = {
    currentSessionId: null,
    updateSessionInList: vi.fn(),
    setShowFileBrowser: vi.fn(),
    setPendingPrompt: vi.fn(),
    sessions: [] as unknown[],
};
vi.mock('../../stores/chatStore', () => {
    const useChatStore = () => storeState;
    useChatStore.getState = () => storeState;
    return { useChatStore };
});

const mockedApi = vi.mocked(api);

const BROWSE: BrowseResponse = {
    current_path: '/home/user/docs',
    parent_path: '/home/user',
    entries: [
        { name: 'report.pdf', path: '/home/user/docs/report.pdf', type: 'file', size: 2048, extension: '.pdf', modified: '' },
    ],
    quick_links: [],
};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.browseFiles.mockResolvedValue(BROWSE);
    mockedApi.previewFile.mockResolvedValue({
        name: 'report.pdf', path: '/home/user/docs/report.pdf', size: 2048, size_display: '2 KB',
        extension: '.pdf', modified: '', is_text: false, preview_lines: [], total_lines: null,
        columns: null, row_count: null,
    });
});

describe('FileBrowser selection gesture', () => {
    it('enables Index/Ask buttons when a file row is clicked (not just the checkbox)', async () => {
        const user = userEvent.setup();
        render(<FileBrowser />);

        const row = await screen.findByText('report.pdf');

        const indexBtn = screen.getByRole('button', { name: /Index Selected/ }) as HTMLButtonElement;
        const askBtn = screen.getByRole('button', { name: /Ask Agent/ }) as HTMLButtonElement;
        // Both actions start disabled with nothing selected.
        expect(indexBtn.disabled).toBe(true);
        expect(askBtn.disabled).toBe(true);

        // Clicking the row itself (the natural select gesture) selects the file.
        await user.click(row);

        await waitFor(() => expect(indexBtn.disabled).toBe(false));
        expect(askBtn.disabled).toBe(false);
        // Row click selects, it does NOT open the preview pane.
        expect(mockedApi.previewFile).not.toHaveBeenCalled();
    });

    it('keeps preview available via the dedicated per-row preview button without selecting', async () => {
        const user = userEvent.setup();
        render(<FileBrowser />);

        await screen.findByText('report.pdf');
        const previewBtn = screen.getByRole('button', { name: /Preview report\.pdf/ });
        await user.click(previewBtn);

        await waitFor(() => expect(mockedApi.previewFile).toHaveBeenCalledWith('/home/user/docs/report.pdf'));
        // Preview alone must not toggle selection.
        expect((screen.getByRole('button', { name: /Index Selected/ }) as HTMLButtonElement).disabled).toBe(true);
    });

    it('toggles selection off when the same row is clicked twice', async () => {
        const user = userEvent.setup();
        render(<FileBrowser />);

        const row = await screen.findByText('report.pdf');
        const indexBtn = screen.getByRole('button', { name: /Index Selected/ }) as HTMLButtonElement;

        await user.click(row);
        await waitFor(() => expect(indexBtn.disabled).toBe(false));
        await user.click(row);
        await waitFor(() => expect(indexBtn.disabled).toBe(true));
    });
});
