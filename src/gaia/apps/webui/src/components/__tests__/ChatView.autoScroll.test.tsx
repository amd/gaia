// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Scrolling up while a reply streams must hold the view; scrolling back down resumes following (#6395). */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Message, Session } from '../../types';

const session: Session = {
    id: 'follow', title: 'Long answer', model: 'qwen', agent_type: 'gaia',
    created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z',
    system_prompt: null, message_count: 1, document_ids: [],
};

const messages: Message[] = [{
    id: 1, session_id: session.id, role: 'user', content: 'Write a long essay',
    created_at: session.created_at, rag_sources: null,
}];

let scrollIntoView: ReturnType<typeof vi.fn>;

beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
        const url = new URL(input, 'http://localhost');
        let body: unknown;
        if (url.pathname === `/api/sessions/${session.id}/messages`) {
            body = { messages, total: messages.length };
        } else if (url.pathname.startsWith('/api/chat/permissions/')) {
            body = { session_id: session.id, mode: 'ask', grants: [] };
        } else if (url.pathname === '/api/chat/active') {
            body = { session_ids: [] };
        } else if (url.pathname === '/api/documents') {
            body = { documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 };
        } else {
            body = {};
        }
        return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
    }));
    scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView });
    useChatStore.setState({
        agents: [], sessions: [session], currentSessionId: session.id,
        messages: [], documents: [], isStreaming: true, streamingContent: '', agentSteps: [],
        isLoadingMessages: false, pendingPrompt: null, systemStatus: null, runningSessionIds: [],
    });
});

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

/** Place the transcript at `scrollTop` in a 2000px list shown through a 500px window. */
function scrollTo(el: HTMLElement, scrollTop: number) {
    Object.defineProperty(el, 'scrollHeight', { configurable: true, value: 2000 });
    Object.defineProperty(el, 'clientHeight', { configurable: true, value: 500 });
    el.scrollTop = scrollTop;
    fireEvent.scroll(el);
}

async function stream(text: string) {
    act(() => { useChatStore.setState({ streamingContent: text }); });
    await act(async () => { await new Promise((r) => setTimeout(r, 150)); });
}

describe('auto-scroll during streaming', () => {
    it('holds the reader where they scrolled, then follows again at the bottom', async () => {
        const { container } = render(<ChatView sessionId={session.id} />);
        await waitFor(() => expect(screen.getByText('Write a long essay')).toBeInTheDocument());
        const list = container.querySelector<HTMLElement>('.messages-scroll')!;

        scrollTo(list, 1500);
        // A small step up stays inside the 80px "near bottom" band, but is still the user leaving.
        scrollTo(list, 1460);
        await stream('First paragraph');
        scrollIntoView.mockClear();
        await stream('First paragraph. Second paragraph');
        expect(scrollIntoView, 'the view snapped back while the user read above').not.toHaveBeenCalled();

        scrollTo(list, 1500);
        await stream('First paragraph. Second paragraph. Third');
        expect(scrollIntoView, 'back at the bottom, the view should follow again').toHaveBeenCalled();
    });

    it('drops a trailing scroll scheduled before the user scrolled up', async () => {
        const { container } = render(<ChatView sessionId={session.id} />);
        await waitFor(() => expect(screen.getByText('Write a long essay')).toBeInTheDocument());
        const list = container.querySelector<HTMLElement>('.messages-scroll')!;
        scrollTo(list, 1500);
        await stream('Intro');

        act(() => { useChatStore.setState({ streamingContent: 'Intro, more' }); });
        scrollIntoView.mockClear();
        scrollTo(list, 900);
        await act(async () => { await new Promise((r) => setTimeout(r, 150)); });
        expect(scrollIntoView).not.toHaveBeenCalled();
    });
});
