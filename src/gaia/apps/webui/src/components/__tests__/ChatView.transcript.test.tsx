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
    system_prompt: null, message_count: 205, document_ids: [],
};
let messages: Message[];
let offsets: number[];
let requests: Array<{ limit: number; offset: number }>;
let beforeMessages: ((url: URL) => Promise<void>) | undefined;

beforeEach(() => {
    // Advance polling deterministically without replacing waitFor's timers.
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    messages = Array.from({ length: 205 }, (_, index) => ({
        id: index + 1, session_id: session.id, role: index % 2 ? 'assistant' : 'user',
        content: `Transcript entry ${index + 1}`, created_at: session.created_at, rag_sources: null,
    }));
    offsets = [];
    requests = [];
    beforeMessages = undefined;
    // Keep the real API service, store, ChatView and message rendering. Only
    // HTTP responses are substituted, using the server's limit/offset contract.
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
        const url = new URL(input, 'http://localhost');
        let body: unknown;
        if (url.pathname === `/api/sessions/${session.id}/messages`) {
            const offset = Number(url.searchParams.get('offset') || 0);
            const limit = Number(url.searchParams.get('limit') || 100);
            offsets.push(offset);
            requests.push({ limit, offset });
            await beforeMessages?.(url);
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
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

it('renders the latest message after closing and reopening a long conversation', async () => {
    const view = render(<ChatView sessionId={session.id} />);
    await waitFor(() => expect(screen.getByText('Transcript entry 205')).toBeInTheDocument());
    expect(screen.getByText('Transcript entry 1')).toBeInTheDocument();
    expect(useChatStore.getState().messages).toHaveLength(205);
    view.unmount();

    messages.push({ ...messages[204], id: 206, role: 'assistant', content: 'Latest reply after reopening' });
    act(() => useChatStore.setState({ messages: [] }));
    render(<ChatView sessionId={session.id} />);
    await waitFor(() => expect(screen.getByText('Latest reply after reopening')).toBeInTheDocument());
    await waitFor(() => expect(useChatStore.getState().messages).toHaveLength(206));
    expect(offsets).toEqual([0, 100, 200, 0, 100, 200]);
});

it('refreshes external messages beyond the first page during normal polling', async () => {
    render(<ChatView sessionId={session.id} />);
    await waitFor(() => expect(screen.getByText('Transcript entry 205')).toBeInTheDocument());
    messages.push({ ...messages[204], id: 206, role: 'assistant', content: 'External reply after page 1' });
    requests = [];
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    await waitFor(() => expect(screen.getByText('External reply after page 1')).toBeInTheDocument());
    expect(useChatStore.getState().messages).toHaveLength(206);
    expect(requests).toEqual([
        { limit: 1, offset: 0 }, { limit: 100, offset: 0 },
        { limit: 100, offset: 100 }, { limit: 6, offset: 200 },
    ]);
});

it('probes unchanged long transcripts once per tick without reloading their pages', async () => {
    render(<ChatView sessionId={session.id} />);
    await waitFor(() => expect(screen.getByText('Transcript entry 205')).toBeInTheDocument());
    requests = [];
    await act(async () => vi.advanceTimersByTimeAsync(6000));
    expect(requests).toEqual([{ limit: 1, offset: 0 }, { limit: 1, offset: 0 }]);
    expect(useChatStore.getState().messages).toHaveLength(205);
});

function deferred() {
    let resolve!: () => void;
    const promise = new Promise<void>((done) => { resolve = done; });
    return { promise, resolve };
}

it('does not overlap a slow count probe with later polls', async () => {
    render(<ChatView sessionId={session.id} />);
    await waitFor(() => expect(screen.getByText('Transcript entry 205')).toBeInTheDocument());
    const gate = deferred();
    beforeMessages = () => gate.promise;
    requests = [];
    await act(async () => vi.advanceTimersByTimeAsync(9000));
    expect(requests).toEqual([{ limit: 1, offset: 0 }]);
    await act(async () => gate.resolve());
    beforeMessages = undefined;
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(requests).toEqual([{ limit: 1, offset: 0 }, { limit: 1, offset: 0 }]);
});

it('does not start polling while the initial paged load is in flight', async () => {
    const gate = deferred();
    beforeMessages = () => gate.promise;
    render(<ChatView sessionId={session.id} />);
    await act(async () => vi.advanceTimersByTimeAsync(9000));
    expect(requests).toEqual([{ limit: 100, offset: 0 }]);
    beforeMessages = undefined;
    await act(async () => gate.resolve());
    await waitFor(() => expect(screen.getByText('Transcript entry 205')).toBeInTheDocument());
    expect(requests).toHaveLength(3);
});

it('keeps the transcript after a failed count probe and retries the next tick', async () => {
    render(<ChatView sessionId={session.id} />);
    await waitFor(() => expect(screen.getByText('Transcript entry 205')).toBeInTheDocument());
    beforeMessages = async () => { throw new Error('Count probe failed'); };
    requests = [];
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(useChatStore.getState().messages).toHaveLength(205);
    beforeMessages = undefined;
    messages.push({ ...messages[204], id: 206, content: 'Reply after count retry' });
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    await waitFor(() => expect(screen.getByText('Reply after count retry')).toBeInTheDocument());
    expect(requests.filter(({ limit }) => limit === 1)).toHaveLength(2);
});

it('does not start an old session reload when its count probe completes after switching', async () => {
    const view = render(<ChatView sessionId={session.id} />);
    await waitFor(() => expect(screen.getByText('Transcript entry 205')).toBeInTheDocument());
    const gate = deferred();
    beforeMessages = () => gate.promise;
    requests = [];
    await act(async () => vi.advanceTimersByTimeAsync(3000));

    const other = { ...session, id: 'other-chat' };
    const otherMessage = { ...messages[0], session_id: other.id, content: 'Other session reply' };
    const oldFetch = fetch;
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
        if (new URL(input, 'http://localhost').pathname === `/api/sessions/${other.id}/messages`) {
            return new Response(JSON.stringify({ messages: [otherMessage], total: 1 }), {
                headers: { 'content-type': 'application/json' },
            });
        }
        return oldFetch(input, init);
    }));
    act(() => useChatStore.setState({ sessions: [session, other], currentSessionId: other.id }));
    view.rerender(<ChatView sessionId={other.id} />);
    await waitFor(() => expect(screen.getByText('Other session reply')).toBeInTheDocument());
    // A changed count would trigger an old-session load without the cancellation check.
    messages.push({ ...messages[204], id: 206, content: 'Old session late reply' });
    beforeMessages = undefined;
    await act(async () => gate.resolve());
    expect(requests).toEqual([{ limit: 1, offset: 0 }]);
    expect(useChatStore.getState().messages.map((message) => message.content)).toEqual(['Other session reply']);
});
