// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';

vi.mock('../../services/api');

const answer = (content: string, over: Partial<Message> = {}): Message => ({
    id: 7, session_id: 's1', role: 'assistant', content, created_at: '2026-11-05T10:00:00Z', rag_sources: null, ...over,
});

const VOICE_REPLY = "<spoken>You had four invoices last week, totaling $3,412.50.</spoken>\n\n| Vendor | Total |\n|---|---|\n| Northwind | $1,980.00 |";

describe('a voice reply in the chat history', () => {
    it('shows the spoken block as a lead line above the detail, never the tags', () => {
        const { container } = render(<MessageBubble message={answer(VOICE_REPLY)} />);
        const lead = container.querySelector('.msg-spoken-lead');
        expect(lead).toHaveTextContent('You had four invoices last week, totaling $3,412.50.');
        expect(container.querySelector('table')).not.toBeNull();
        expect(container).not.toHaveTextContent('<spoken>');
        expect(container).not.toHaveTextContent('</spoken>');
        // The lead comes first.
        expect(lead?.compareDocumentPosition(container.querySelector('table')!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    });

    it('while streaming: the lead as it is written, and no half tag', () => {
        const { container, rerender } = render(<MessageBubble message={answer('<spok')} isStreaming />);
        expect(container).not.toHaveTextContent('<spok');
        rerender(<MessageBubble message={answer('<spoken>Four invoi')} isStreaming />);
        expect(container.querySelector('.msg-spoken-lead')).toHaveTextContent('Four invoi');
        expect(container).not.toHaveTextContent('<spoken>');
    });

    it('a reply without a spoken block renders as before', () => {
        const { container } = render(<MessageBubble message={answer('Just **text**.')} />);
        expect(container.querySelector('.msg-spoken-lead')).toBeNull();
        expect(container.querySelector('strong')).toHaveTextContent('text');
    });

    it('Copy takes the words without the tags', () => {
        const writeText = vi.fn(async () => undefined);
        Object.assign(navigator, { clipboard: { writeText } });
        render(<MessageBubble message={answer('<spoken>Done.</spoken>\nThe file is saved.')} />);
        fireEvent.click(screen.getByRole('button', { name: 'Copy message' }));
        expect(writeText).toHaveBeenCalledWith('Done.\n\nThe file is saved.');
    });
});
