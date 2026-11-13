// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { DocumentLibrary } from '../DocumentLibrary';
import * as api from '../../services/api';

vi.mock('../../services/api');

afterEach(() => vi.restoreAllMocks());

async function renderLibrary() {
    vi.mocked(api.listDocuments).mockResolvedValue({ documents: [], total: 0 } as never);
    vi.mocked(api.uploadDocumentBlob).mockResolvedValue({ id: 'd1', filename: 'notes.txt' } as never);
    const { container } = render(<DocumentLibrary />);
    const zone = await screen.findByRole('button', { name: 'Drop files here to add them' });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    return { zone, input };
}

it('opens the file picker when the drop box is clicked or Enter is pressed', async () => {
    const { zone, input } = await renderLibrary();
    const open = vi.spyOn(input, 'click');

    fireEvent.click(zone);
    fireEvent.keyDown(zone, { key: 'Enter' });

    expect(open).toHaveBeenCalledTimes(2);
});

it('adds the file the user picks', async () => {
    const { input } = await renderLibrary();
    const file = new File(['hello'], 'notes.txt', { type: 'text/plain' });

    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(api.uploadDocumentBlob).toHaveBeenCalledWith(file));
});
