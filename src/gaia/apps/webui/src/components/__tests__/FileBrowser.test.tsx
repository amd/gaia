// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FileBrowser } from '../FileBrowser';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { BrowseResponse } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const BROWSE_RESPONSE: BrowseResponse = {
    current_path: '/home/user/docs',
    parent_path: '/home/user',
    entries: [
        { name: 'report.pdf', path: '/home/user/docs/report.pdf', type: 'file', size: 1024, extension: '.pdf', modified: '' },
        { name: 'notes.txt', path: '/home/user/docs/notes.txt', type: 'file', size: 512, extension: '.txt', modified: '' },
    ],
    quick_links: [],
};

const FILE_PREVIEW = {
    path: '/home/user/docs/report.pdf',
    name: 'report.pdf',
    size: 1024,
    size_display: '1 KB',
    extension: '.pdf',
    modified: '',
    is_text: true,
    preview_lines: ['line 1', 'line 2'],
    total_lines: 2,
    columns: null,
    row_count: null,
};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.browseFiles.mockResolvedValue(BROWSE_RESPONSE);
    mockedApi.previewFile.mockResolvedValue(FILE_PREVIEW);

    useChatStore.setState({
        currentSessionId: 'session-1',
        sessions: [],
        showFileBrowser: true,
    });
});

describe('FileBrowser row selection', () => {
    it('clicking a file row selects it and enables Index Selected and Ask Agent', async () => {
        render(<FileBrowser />);

        // Wait for the directory listing to load
        const fileName = await screen.findByText('report.pdf');
        const row = fileName.closest('.fb-entry') as HTMLElement;
        expect(row).toBeTruthy();

        const indexBtn = screen.getByRole('button', { name: /Index Selected/i });
        const askBtn = screen.getByRole('button', { name: /Ask Agent/i });

        // Both buttons disabled before selection
        expect(indexBtn).toBeDisabled();
        expect(askBtn).toBeDisabled();

        // Click the row to select
        fireEvent.click(row);

        // After clicking the row the file should be selected
        await waitFor(() => {
            expect(indexBtn).not.toBeDisabled();
            expect(askBtn).not.toBeDisabled();
        });

        // The checkbox inside the row should now be checked
        const checkbox = within(row).getByRole('checkbox');
        expect(checkbox).toBeChecked();
    });

    it('clicking the preview icon previews without selecting the file', async () => {
        render(<FileBrowser />);

        const fileName = await screen.findByText('report.pdf');
        const row = fileName.closest('.fb-entry') as HTMLElement;
        expect(row).toBeTruthy();

        const indexBtn = screen.getByRole('button', { name: /Index Selected/i });
        const askBtn = screen.getByRole('button', { name: /Ask Agent/i });

        // Click the preview button inside the row
        const previewBtn = within(row).getByRole('button', { name: /Preview/i });
        fireEvent.click(previewBtn);

        // previewFile should have been called
        await waitFor(() => {
            expect(mockedApi.previewFile).toHaveBeenCalledWith('/home/user/docs/report.pdf');
        });

        // Checkbox should stay unchecked
        const checkbox = within(row).getByRole('checkbox');
        expect(checkbox).not.toBeChecked();

        // Buttons should still be disabled (no selection happened)
        expect(indexBtn).toBeDisabled();
        expect(askBtn).toBeDisabled();
    });

    it('checkbox toggles selection on and off (regression guard)', async () => {
        render(<FileBrowser />);

        const fileName = await screen.findByText('notes.txt');
        const row = fileName.closest('.fb-entry') as HTMLElement;
        expect(row).toBeTruthy();

        const indexBtn = screen.getByRole('button', { name: /Index Selected/i });
        const askBtn = screen.getByRole('button', { name: /Ask Agent/i });

        const checkbox = within(row).getByRole('checkbox');

        // Initially unchecked, buttons disabled
        expect(checkbox).not.toBeChecked();
        expect(indexBtn).toBeDisabled();
        expect(askBtn).toBeDisabled();

        // Click checkbox to select
        fireEvent.click(checkbox);

        await waitFor(() => {
            expect(checkbox).toBeChecked();
            expect(indexBtn).not.toBeDisabled();
            expect(askBtn).not.toBeDisabled();
        });

        // Click checkbox again to deselect
        fireEvent.click(checkbox);

        await waitFor(() => {
            expect(checkbox).not.toBeChecked();
            expect(indexBtn).toBeDisabled();
            expect(askBtn).toBeDisabled();
        });
    });
});

describe('FileBrowser search truncation', () => {
    const runSearch = async (truncated: boolean) => {
        mockedApi.searchFiles.mockResolvedValue({
            results: [
                { name: 'report.pdf', path: '/home/user/docs/report.pdf', size: 1024, size_display: '1 KB', extension: '.pdf', modified: '', directory: '/home/user/docs' },
            ],
            total: 1,
            query: 'report',
            searched_locations: ['/home/user/Documents'],
            truncated,
        });
        render(<FileBrowser />);
        await screen.findByText('report.pdf');
        const input = screen.getByPlaceholderText('Search files on your PC...');
        fireEvent.change(input, { target: { value: 'report' } });
        fireEvent.keyDown(input, { key: 'Enter' });
        await screen.findByText(/Found 1 result/);
    };

    it('says the search stopped early when the backend truncated the walk', async () => {
        await runSearch(true);
        expect(screen.getByText(/Search stopped early/)).toBeInTheDocument();
    });

    it('shows no truncation note for a complete search', async () => {
        await runSearch(false);
        expect(screen.queryByText(/Search stopped early/)).not.toBeInTheDocument();
    });
});
