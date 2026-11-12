// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Attach an image and a text file, send, and the sent message shows the
 * picture and links the file, using the URLs the upload endpoint returns.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, renderHook, waitFor } from '@testing-library/react';

const apiBase = vi.hoisted(() => ({ value: '/api' }));
vi.mock('../../utils/apiBase', () => ({ getApiBase: () => apiBase.value }));
vi.mock('../../services/api', () => ({
    uploadFile: vi.fn(async (file: File) => {
        const ext = file.name.slice(file.name.lastIndexOf('.'));
        const id = file.type.startsWith('image/')
            ? '0b9e7a52-1c2d-4e5f-8a9b-0c1d2e3f4a5b'
            : '6f1e2d3c-4b5a-4968-8776-655443322110';
        return {
            filename: `${id}${ext}`,
            original_name: file.name,
            url: `/api/files/uploads/${id}${ext}`,
            size: file.size,
            content_type: file.type,
            is_image: file.type.startsWith('image/'),
        };
    }),
}));

import { useAttachments } from '../../hooks/useAttachments';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';

async function sentContent(): Promise<string> {
    globalThis.URL.createObjectURL ??= () => 'blob:preview';
    const { result } = renderHook(() => useAttachments());
    act(() => {
        result.current.addFiles([
            new File([new Uint8Array([137, 80, 78, 71])], 'sunset.png', { type: 'image/png' }),
            new File(['hello'], 'notes.txt', { type: 'text/plain' }),
        ]);
    });
    await waitFor(() => expect(result.current.hasUploaded && !result.current.uploading).toBe(true));
    return result.current.compose('What color is this image?');
}

function bubble(content: string) {
    const message: Message = {
        id: 1,
        session_id: 'sess-1',
        role: 'user',
        content,
        created_at: '2026-10-27T00:00:00.000Z',
        rag_sources: null,
    };
    return render(<MessageBubble message={message} />);
}

describe.each([
    ['browser', '/api', '/api/files/uploads/'],
    ['desktop app', 'http://127.0.0.1:51234/api', 'http://127.0.0.1:51234/api/files/uploads/'],
])('a sent message with attachments (%s)', (_label, base, expected) => {
    beforeEach(() => { apiBase.value = base; });

    it('shows the uploaded image and links the file to the backend', async () => {
        const { container, getByText } = bubble(await sentContent());
        const img = container.querySelector('.md-content img');
        expect(img?.getAttribute('src')).toBe(`${expected}0b9e7a52-1c2d-4e5f-8a9b-0c1d2e3f4a5b.png`);
        expect(img?.getAttribute('alt')).toBe('sunset.png');
        expect(getByText('notes.txt').closest('a')?.getAttribute('href')).toBe(
            `${expected}6f1e2d3c-4b5a-4968-8776-655443322110.txt`,
        );
    });

    it('does not let a look-alike link in the same message through', () => {
        const { getByText } = bubble(
            '[a](javascript:alert(1)) [b](/api/files/uploads/../x.txt) [c](file:///C:/x.png)',
        );
        for (const label of ['a', 'b', 'c']) {
            const href = getByText(label).closest('a')?.getAttribute('href') ?? '';
            expect(href).toBe('');
        }
    });
});
