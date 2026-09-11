// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Message, Session } from '../../types';

const session: Session = {
    id: 'long-chat', title: 'Existing chat', model: 'qwen', agent_type: 'chat',
    created_at: '2026-09-11T00:00:00Z', updated_at: '2026-09-11T00:00:00Z',
    system_prompt: null, message_count: 105, document_ids: [],
};
let messages: Message[];
let offsets: number[];

beforeEach(() => {
    messages = Array.from({ length: 105 }, (_, index) => ({
        id: index + 1, session_id: session.id, role: index % 2 ? 'assistant' : 'user',
        content: `Transcript entry ${index + 1}`, created_at: session.created_at, rag_sources: null,
    }));
    offsets = [];
    // Keep the real API service, store, ChatView and message rendering. Only
    // HTTP responses are substituted, using the server's limit/offset contract.
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
        const url = new URL(input, 'http://localhost');
        let body: unknown;
        if (url.pathname === `/api/sessions/${session.id}/messages`) {
            const offset = Number(url.searchParams.get('offset') || 0);
            const limit = Number(url.searchParams.get('limit') || 100);
            offsets.push(offset);
            body = { messages: messages.slice(offset, offset + limit), total: messages.length };
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

it('renders the latest message after closing and reopening a long conversation', async () => {
    const view = render(<ChatView sessionId={session.id} />);
    await waitFor(() => expect(screen.getByText('Transcript entry 105')).toBeInTheDocument());
    expect(screen.getByText('Transcript entry 1')).toBeInTheDocument();
    expect(useChatStore.getState().messages).toHaveLength(105);
    view.unmount();

    messages.push({ ...messages[104], id: 106, role: 'assistant', content: 'Latest reply after reopening' });
    act(() => useChatStore.setState({ messages: [] }));
    render(<ChatView sessionId={session.id} />);
    await waitFor(() => expect(screen.getByText('Latest reply after reopening')).toBeInTheDocument());
    await waitFor(() => expect(useChatStore.getState().messages).toHaveLength(106));
    expect(offsets).toEqual([0, 100, 0, 100]);
});

it('refreshes external messages beyond the first page during normal polling', async () => {
    render(<ChatView sessionId={session.id} />);
    await waitFor(() => expect(screen.getByText('Transcript entry 105')).toBeInTheDocument());
    messages.push({ ...messages[104], id: 106, role: 'assistant', content: 'External reply after page 1' });
    await waitFor(() => expect(screen.getByText('External reply after page 1')).toBeInTheDocument(), {
        timeout: 5000,
    });
    expect(useChatStore.getState().messages).toHaveLength(106);
});
