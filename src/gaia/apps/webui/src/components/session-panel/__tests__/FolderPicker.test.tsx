// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** GAIA's own folder picker, used by the web version: hidden folders stay out of the way. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FolderPicker } from '../FolderPicker';
import * as api from '../../../services/api';
import type { BrowseResponse, FileEntry } from '../../../types';

vi.mock('../../../services/api');

const folder = (name: string): FileEntry => ({
    name, path: `/home/u/${name}`, type: 'folder', size: 0, extension: null, modified: null,
} as FileEntry);

const listing = (entries: FileEntry[], current_path = '/home/u'): BrowseResponse => ({
    current_path, parent_path: null, entries, quick_links: [],
} as BrowseResponse);

const list = () => screen.getByRole('list');

beforeEach(() => vi.clearAllMocks());

describe('FolderPicker', () => {
    it('hides dot folders until Show hidden is on', async () => {
        vi.mocked(api.browseFiles).mockResolvedValue(listing([folder('.config'), folder('Documents')]));
        render(<FolderPicker onPick={vi.fn()} onClose={vi.fn()} />);

        expect(await within(list()).findByRole('button', { name: 'Documents' })).toBeInTheDocument();
        expect(within(list()).queryByRole('button', { name: '.config' })).not.toBeInTheDocument();
        const toggle = screen.getByRole('checkbox', { name: 'Show hidden' });
        expect(toggle).not.toBeChecked();

        fireEvent.click(toggle);
        expect(within(list()).getByRole('button', { name: '.config' })).toBeInTheDocument();

        fireEvent.click(toggle);
        expect(within(list()).queryByRole('button', { name: '.config' })).not.toBeInTheDocument();
    });

    it('says a folder has only hidden folders instead of calling it empty', async () => {
        vi.mocked(api.browseFiles).mockResolvedValue(listing([folder('.cache')]));
        render(<FolderPicker onPick={vi.fn()} onClose={vi.fn()} />);

        expect(await screen.findByText('Only hidden folders here. Turn on Show hidden to see them.')).toBeInTheDocument();
        expect(screen.queryByText('No folders inside.')).not.toBeInTheDocument();
    });

    it('still says a folder is empty when it has no folders at all', async () => {
        vi.mocked(api.browseFiles).mockResolvedValue(listing([]));
        render(<FolderPicker onPick={vi.fn()} onClose={vi.fn()} />);

        expect(await screen.findByText('No folders inside.')).toBeInTheDocument();
    });

    it('goes into a hidden folder by its typed path and uses it', async () => {
        vi.mocked(api.browseFiles).mockImplementation(async (path?: string) =>
            path ? listing([], path) : listing([folder('.ssh')]));
        const onPick = vi.fn();
        render(<FolderPicker onPick={onPick} onClose={vi.fn()} />);

        const input = await screen.findByLabelText('Folder path');
        await waitFor(() => expect(input).toHaveValue('/home/u'));
        fireEvent.change(input, { target: { value: '/home/u/.ssh' } });
        fireEvent.click(screen.getByRole('button', { name: 'Go' }));
        await waitFor(() => expect(api.browseFiles).toHaveBeenLastCalledWith('/home/u/.ssh'));
        await waitFor(() => expect(input).toHaveValue('/home/u/.ssh'));

        fireEvent.click(screen.getByRole('button', { name: 'Use this folder' }));
        expect(onPick).toHaveBeenCalledWith('/home/u/.ssh');
    });

    it('starts with hidden folders tucked away each time it opens', async () => {
        vi.mocked(api.browseFiles).mockResolvedValue(listing([folder('.config'), folder('Documents')]));
        const { unmount } = render(<FolderPicker onPick={vi.fn()} onClose={vi.fn()} />);
        fireEvent.click(await screen.findByRole('checkbox', { name: 'Show hidden' }));
        unmount();

        render(<FolderPicker onPick={vi.fn()} onClose={vi.fn()} />);
        expect(await screen.findByRole('checkbox', { name: 'Show hidden' })).not.toBeChecked();
    });
});
