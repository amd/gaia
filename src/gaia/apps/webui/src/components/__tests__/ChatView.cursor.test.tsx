// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * "A blinking cursor means output is still streaming — it is a status
 * indicator, never decoration, and never runs when nothing is being written."
 * — docs/spec/gaia-design-language.mdx, Typography and spacing.
 *
 * The rule was written down before it was true: ChatView used to hand the last
 * assistant message a cursor of its own, so reopening a finished conversation
 * left one blinking against a transcript nothing was writing to. The two
 * assertions below are the rule stated as behaviour — a settled transcript
 * carries no cursor, a live one does — so the mechanism cannot come back
 * silently.
 */

import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { MessageBubble } from '../MessageBubble';
import { useChatStore } from '../../stores/chatStore';
import type { Message, Session } from '../../types';

const session: Session = {
    id: 'settled', title: 'Yesterday', model: 'qwen', agent_type: 'chat',
    created_at: '2026-09-11T00:00:00Z', updated_at: '2026-09-11T00:00:00Z',
    system_prompt: null, message_count: 2, document_ids: [],
};

const messages: Message[] = [
    {
        id: 1, session_id: session.id, role: 'user', content: 'What did we decide?',
        created_at: session.created_at, rag_sources: null,
    },
    {
        id: 2, session_id: session.id, role: 'assistant', content: 'We shipped it.',
        created_at: session.created_at, rag_sources: null,
    },
];

beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
        const url = new URL(input, 'http://localhost');
        let body: unknown;
        if (url.pathname === `/api/sessions/${session.id}/messages`) {
            body = { messages, total: messages.length };
        } else if (url.pathname === '/api/chat/active') {
            body = { session_ids: [] };
        } else if (url.pathname === '/api/documents') {
            body = { documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 };
        } else {
            throw new Error(`Unexpected request: ${url}`);
        }
        return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
    }));
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true, value: vi.fn(),
    });
    useChatStore.setState({
        agents: [], activeAgentId: 'chat', sessions: [session], currentSessionId: session.id,
        messages: [], documents: [], isStreaming: false, streamingContent: '', agentSteps: [],
        isLoadingMessages: false, pendingPrompt: null, systemStatus: null, runningSessionIds: [],
    });
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe('the cursor reports writing, and nothing else', () => {
    it('leaves no cursor on a finished conversation', async () => {
        const { container } = render(<ChatView sessionId={session.id} />);
        await waitFor(() => expect(screen.getByText('We shipped it.')).toBeInTheDocument());

        expect(
            Array.from(container.querySelectorAll('.cursor')).length,
            'nothing is being written — the cursor is decoration here',
        ).toBe(0);
    });

    it('still shows one while the answer is arriving', () => {
        const { container } = render(
            <MessageBubble
                message={{ ...messages[1], id: -1, content: 'We shipped' }}
                isStreaming
            />,
        );

        expect(container.querySelectorAll('.cursor').length).toBe(1);
    });
});
