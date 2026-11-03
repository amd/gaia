// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Scrolling up while a reply streams must hold the view; scrolling back down resumes following. */

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

/** Size the transcript; resizes and content changes fire no scroll event unless scrollTop moves. */
function layout(el: HTMLElement, scrollHeight: number, clientHeight = 500) {
    Object.defineProperty(el, 'scrollHeight', { configurable: true, value: scrollHeight });
    Object.defineProperty(el, 'clientHeight', { configurable: true, value: clientHeight });
}

/** Place the transcript at `scrollTop` (default: a 2000px list shown through a 500px window). */
function scrollTo(el: HTMLElement, scrollTop: number, scrollHeight = 2000, clientHeight = 500) {
    layout(el, scrollHeight, clientHeight);
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

async function followsNext(text: string) {
    scrollIntoView.mockClear();
    await stream(text);
    return scrollIntoView.mock.calls.length > 0;
}

async function mountList() {
    const { container } = render(<ChatView sessionId={session.id} />);
    await waitFor(() => expect(screen.getByText('Write a long essay')).toBeInTheDocument());
    return container.querySelector<HTMLElement>('.messages-scroll')!;
}

describe('auto-scroll edge cases', () => {
    it('keeps following when a card above collapses and the browser pulls scrollTop up', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        // Mid smooth-scroll (20px short of the end), a 300px card above collapses; anchoring moves scrollTop up.
        scrollTo(list, 1580, 2100);
        scrollTo(list, 1280, 1800);
        expect(await followsNext('After the collapse')).toBe(true);
    });

    it('keeps following when content above grows and anchoring pushes scrollTop past the 80px band', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        scrollTo(list, 1900, 2600);
        expect(await followsNext('After an image above loaded')).toBe(true);
    });

    it('keeps following through the intermediate events of its own smooth scroll', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        scrollTo(list, 1800, 3000);
        scrollTo(list, 2200, 3000);
        expect(await followsNext('Still streaming')).toBe(true);
    });

    it('keeps following when the window gets shorter at the bottom', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        layout(list, 2000, 300);
        expect(await followsNext('After the resize')).toBe(true);
    });

    it('keeps following when a wider window shortens the content and clamps scrollTop', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        scrollTo(list, 1100, 1600);
        expect(await followsNext('After the resize')).toBe(true);
    });

    it('resumes at a fractional bottom left by browser zoom', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        scrollTo(list, 1200);
        expect(await followsNext('Held')).toBe(false);
        scrollTo(list, 1499.4);
        expect(await followsNext('Following again')).toBe(true);
    });

    it('stays put when the reader expands a tool call above', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        scrollTo(list, 900);
        // Anchoring pushes scrollTop down as content above grows; still far from the bottom.
        scrollTo(list, 1300, 2400);
        expect(await followsNext('More text')).toBe(false);
    });

    it('stays put when the reader expands a tool call while inside the 80px band', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        scrollTo(list, 1460);
        layout(list, 2400);
        expect(await followsNext('More text')).toBe(false);
    });

    it('stays put when the window is resized while the reader is above', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        scrollTo(list, 900);
        layout(list, 2000, 300);
        expect(await followsNext('After a shorter window')).toBe(false);
        scrollTo(list, 700, 1800, 300);
        expect(await followsNext('After a wider window')).toBe(false);
    });

    it('stays put while tool steps stream in', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        scrollTo(list, 1000);
        scrollIntoView.mockClear();
        act(() => {
            useChatStore.setState({
                agentSteps: [{ id: 1, type: 'tool', label: 'Reading file', tool: 'read_file', active: true, timestamp: 1 }],
            });
        });
        await act(async () => { await new Promise((r) => setTimeout(r, 150)); });
        act(() => {
            useChatStore.setState({
                agentSteps: [{
                    id: 1, type: 'tool', label: 'Reading file', tool: 'read_file',
                    active: false, success: true, result: 'x'.repeat(500), timestamp: 1,
                }],
            });
        });
        await act(async () => { await new Promise((r) => setTimeout(r, 150)); });
        expect(scrollIntoView).not.toHaveBeenCalled();
    });

    it('stays put on every small step up, one after another', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        for (const top of [1498, 1496, 1493]) {
            scrollTo(list, top);
            expect(await followsNext(`step ${top}`)).toBe(false);
        }
    });

    it('does not resume on a downward scroll that stops above the 80px band', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        scrollTo(list, 800);
        scrollTo(list, 1300);
        expect(await followsNext('Still reading')).toBe(false);
        scrollTo(list, 1450);
        expect(await followsNext('Back near the end')).toBe(true);
    });

    it('stops following when the reader scrolls up in the same frame a card above collapses', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        // The card takes 300px with it; the reader's own 200px up is what remains.
        scrollTo(list, 1000, 1700);
        expect(await followsNext('More text')).toBe(false);
    });

    it('stays put when content above a reader inside the 80px band grows', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        scrollTo(list, 1460);
        // An image loads above: anchoring pushes scrollTop down by its height, 40px short of the end still.
        scrollTo(list, 1760, 2300);
        expect(await followsNext('More text')).toBe(false);
    });

    it('resumes from the scroll-to-bottom button', async () => {
        const list = await mountList();
        scrollTo(list, 1500);
        scrollTo(list, 200);
        fireEvent.click(screen.getByRole('button', { name: 'Scroll to bottom' }));
        expect(await followsNext('Following')).toBe(true);
    });
});
