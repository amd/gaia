// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * History persistence / rehydration contract for the `needs_confirmation`
 * card (issue #2109, Increment 2, stateless D1).
 *
 * Persistence must ride the pre-existing generic `Message.cards` /
 * `RenderCardData` path from #2108 — no bespoke storage field. This file
 * proves that a `needs_confirmation` card previously persisted onto a
 * finalized message (e.g. loaded from `GET /sessions/:id/messages` on a
 * fresh session-history fetch, with NO live SSE stream in flight) renders
 * correctly through MessageBubble's existing `message.cards` -> RenderCard
 * path, exactly like MessageBubble.cards.test.tsx already proves for
 * `table`/`list`/etc.
 *
 * `needs_confirmation` is not registered in CARD_REGISTRY yet (see
 * render/registry.tsx), so RenderCard currently falls back to
 * UnsupportedCard for this key — these assertions are expected to fail
 * (red) until the Increment 2 implementer adds the registry entry.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { Message, RenderCardData } from '../../types';

const minimalMessage: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'assistant',
    content: 'Here is the status of your request.',
    created_at: '2026-07-16T00:00:00.000Z',
    rag_sources: null,
};

const confirmationCard: RenderCardData = {
    render: 'needs_confirmation',
    data: {
        action: 'send_now',
        summary: 'Send the drafted email to alice@example.com now?',
    },
};

describe('needs_confirmation card rehydration from persisted history (#2109)', () => {
    it('renders action + summary from message.cards with no live stream involved', () => {
        render(<MessageBubble message={{ ...minimalMessage, cards: [confirmationCard] }} />);

        expect(screen.getByText('Send the drafted email to alice@example.com now?')).toBeInTheDocument();
        // Must be the real card, not the "no card registered" fallback.
        expect(screen.queryByText('Unsupported card type: "needs_confirmation"')).not.toBeInTheDocument();
    });

    it('positions the rehydrated card above the markdown content, like every other render/ card', () => {
        const { container } = render(
            <MessageBubble message={{ ...minimalMessage, cards: [confirmationCard] }} />,
        );

        const card = container.querySelector('.render-card');
        const mdContent = container.querySelector('.md-content');
        expect(card).not.toBeNull();
        expect(mdContent).not.toBeNull();

        const position = card!.compareDocumentPosition(mdContent!);
        expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('rehydrates a fresh (non-dismissed) card even if a prior instance in this session had been dismissed', () => {
        // Dismiss is local component state only (D1) — it is never persisted
        // to the backend or the store, so a fresh mount (e.g. reopening the
        // session, or a page reload that re-fetches history) must always
        // show the card in its original, non-dismissed state.
        const { unmount } = render(
            <MessageBubble message={{ ...minimalMessage, cards: [confirmationCard] }} />,
        );
        unmount();

        render(<MessageBubble message={{ ...minimalMessage, cards: [confirmationCard] }} />);

        expect(screen.getByText('Send the drafted email to alice@example.com now?')).toBeInTheDocument();
    });
});
