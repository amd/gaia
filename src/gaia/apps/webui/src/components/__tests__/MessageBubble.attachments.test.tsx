// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A message with attachments shows the image as a thumbnail and a link that opens the file inside the app,
 * using the links the composer writes for uploaded files.
 */

import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';

const userMessage: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'user',
    content: [
        'hi',
        '',
        '![sunset.png](/api/files/uploads/0b9e7a52-1c2d-4e5f-8a9b-0c1d2e3f4a5b.png)',
        '[notes.txt](/api/files/uploads/6f1e2d3c-4b5a-4968-8776-655443322110.txt)',
    ].join('\n'),
    created_at: '2026-10-27T00:00:00.000Z',
    rag_sources: null,
};

describe('attachments in a sent message', () => {
    it('shows the uploaded image from the server as a thumbnail', () => {
        const { container } = render(<MessageBubble message={userMessage} />);
        const img = container.querySelector('.image-thumb img');
        expect(img?.getAttribute('src')).toBe('/api/files/uploads/0b9e7a52-1c2d-4e5f-8a9b-0c1d2e3f4a5b.png');
        expect(container.querySelector('.image-thumb')?.getAttribute('aria-label')).toContain('sunset.png');
    });

    it('opens the uploaded text file inside the app, never in a browser window', async () => {
        const fetchMock = vi.fn().mockResolvedValue(new Response('Tomatoes need six hours of sun.', { status: 200 }));
        vi.stubGlobal('fetch', fetchMock);
        const open = vi.spyOn(window, 'open').mockReturnValue(null);
        try {
            const { getByText, findByRole, container } = render(<MessageBubble message={userMessage} />);
            const link = getByText('notes.txt').closest('button');
            expect(link).not.toBeNull();
            expect(container.querySelector('a[href*="uploads/6f1e"]')).toBeNull();

            fireEvent.click(link!);

            const dialog = await findByRole('dialog');
            await waitFor(() => expect(dialog).toHaveTextContent('Tomatoes need six hours of sun.'));
            expect(fetchMock.mock.calls[0][0]).toBe('/api/files/uploads/6f1e2d3c-4b5a-4968-8776-655443322110.txt');
            expect(open).not.toHaveBeenCalled();
        } finally {
            vi.unstubAllGlobals();
            open.mockRestore();
        }
    });

    it('says so when the file is not text', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x00, 0xff, 0xfe]), { status: 200 })));
        try {
            const { getByText, findByRole } = render(<MessageBubble message={userMessage} />);
            fireEvent.click(getByText('notes.txt').closest('button')!);
            const dialog = await findByRole('dialog');
            await waitFor(() => expect(dialog).toHaveTextContent("notes.txt can't be shown here."));
        } finally {
            vi.unstubAllGlobals();
        }
    });
});
