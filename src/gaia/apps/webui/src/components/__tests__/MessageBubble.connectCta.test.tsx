// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A reply that says a mailbox is not connected carries a Connect button. */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';

const reply = (content: string): Message => ({
    id: 1,
    session_id: 'sess-1',
    role: 'assistant',
    content,
    created_at: '2026-07-16T00:00:00.000Z',
    rag_sources: null,
});

describe('MessageBubble connect prompt', () => {
    it('offers Connect Google under a reply that says Google is not connected', () => {
        render(
            <MessageBubble
                message={reply(
                    'NOT_CONNECTED: google is not currently connected. Connect it in Settings → Connectors → Google.',
                )}
            />,
        );

        expect(screen.getByRole('region', { name: 'Connect email account' })).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Connect Google' })).toBeTruthy();
        expect(screen.queryByRole('button', { name: 'Connect Microsoft' })).toBeNull();
    });

    it('offers every provider when the reply names none', () => {
        render(<MessageBubble message={reply('AUTH_REQUIRED: connect an email account to continue.')} />);

        for (const name of ['Connect Google', 'Connect Microsoft']) {
            expect(screen.getByRole('button', { name })).toBeTruthy();
        }
        // GAIA cannot read a work Microsoft 365 mailbox, so it is not offered.
        expect(screen.queryByRole('button', { name: 'Connect Microsoft 365' })).toBeNull();
    });

    it('shows no connect prompt under an ordinary reply', () => {
        render(<MessageBubble message={reply('Your inbox has three unread messages.')} />);

        expect(screen.queryByRole('region', { name: 'Connect email account' })).toBeNull();
    });

    it('shows no connect prompt while the reply is still streaming', () => {
        render(<MessageBubble isStreaming message={reply('NOT_CONNECTED: google is not currently connected.')} />);

        expect(screen.queryByRole('region', { name: 'Connect email account' })).toBeNull();
    });
});
