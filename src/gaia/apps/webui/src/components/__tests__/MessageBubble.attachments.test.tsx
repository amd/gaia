// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A message with attachments shows the image and a working link to the file,
 * using the links the composer writes for uploaded files.
 */

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
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
    it('shows the uploaded image from the server', () => {
        const { container } = render(<MessageBubble message={userMessage} />);
        const img = container.querySelector('.md-content img');
        expect(img?.getAttribute('src')).toBe('/api/files/uploads/0b9e7a52-1c2d-4e5f-8a9b-0c1d2e3f4a5b.png');
        expect(img?.getAttribute('alt')).toBe('sunset.png');
    });

    it('links the uploaded text file to the file, not the app home', () => {
        const { getByText } = render(<MessageBubble message={userMessage} />);
        expect(getByText('notes.txt').closest('a')?.getAttribute('href')).toBe(
            '/api/files/uploads/6f1e2d3c-4b5a-4968-8776-655443322110.txt',
        );
    });
});
