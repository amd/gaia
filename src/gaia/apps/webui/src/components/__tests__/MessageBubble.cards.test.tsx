// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Contract tests for MessageBubble's `cards` rendering (issue #2108):
 * `message.cards` (finalized) wins over the `cards` prop (live-streaming),
 * both render via RenderCard, and they are positioned ABOVE the markdown
 * content in DOM order.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { Message, RenderCardData } from '../../types';

const minimalMessage: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'assistant',
    content: 'hello world',
    created_at: '2026-07-16T00:00:00.000Z',
    rag_sources: null,
};

// A `table` card whose rendered cell text is unique and unambiguous —
// success is `getByText('cell-val')` / `getByRole('table')`.
const messageTableCard: RenderCardData = {
    render: 'table',
    data: { columns: ['Col1'], rows: [['cell-val']] },
};

// A second, distinct table card so the "message wins over prop" test can
// prove which payload actually rendered.
const propTableCard: RenderCardData = {
    render: 'table',
    data: { columns: ['Col1'], rows: [['prop-only-val']] },
};

describe('MessageBubble cards rendering (#2108)', () => {
    it('renders message.cards above the markdown content', () => {
        const { container } = render(
            <MessageBubble message={{ ...minimalMessage, cards: [messageTableCard] }} />
        );

        expect(screen.getByRole('table')).toBeInTheDocument();
        expect(screen.getByText('cell-val')).toBeInTheDocument();

        const card = container.querySelector('.render-card');
        const mdContent = container.querySelector('.md-content');
        expect(card).not.toBeNull();
        expect(mdContent).not.toBeNull();

        // card precedes md-content in DOM order: md-content must FOLLOW the card.
        const position = card!.compareDocumentPosition(mdContent!);
        expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('renders the cards prop when message.cards is undefined (live-streaming path)', () => {
        render(<MessageBubble message={minimalMessage} cards={[messageTableCard]} />);

        expect(screen.getByRole('table')).toBeInTheDocument();
        expect(screen.getByText('cell-val')).toBeInTheDocument();
    });

    it('message.cards wins when both message.cards and the cards prop are set', () => {
        render(
            <MessageBubble
                message={{ ...minimalMessage, cards: [messageTableCard] }}
                cards={[propTableCard]}
            />
        );

        expect(screen.getByText('cell-val')).toBeInTheDocument();
        expect(screen.queryByText('prop-only-val')).not.toBeInTheDocument();
    });

    it('renders no card when neither message.cards nor the cards prop are set', () => {
        const { container } = render(<MessageBubble message={minimalMessage} />);

        expect(container.querySelector('.render-card')).toBeNull();
    });

    it('falls back to the Unsupported card for an unknown render key in message.cards', () => {
        render(
            <MessageBubble
                message={{ ...minimalMessage, cards: [{ render: 'not_a_real_card', data: {} }] }}
            />
        );

        expect(screen.getByText('Unsupported card type: "not_a_real_card"')).toBeInTheDocument();
    });
});
