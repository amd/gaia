// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A long chat stays smooth: a streaming reply and typing redraw only what changed, not every message. */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Message, Session } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const renders = vi.hoisted(() => new Map<number, number>());
vi.mock('../MessageBubble', async (importOriginal) => {
    const real = await importOriginal<typeof import('../MessageBubble')>();
    return {
        ...real,
        MessageBubble: (props: Parameters<typeof real.MessageBubble>[0]) => {
            renders.set(props.message.id, (renders.get(props.message.id) ?? 0) + 1);
            return real.MessageBubble(props);
        },
    };
});

const mockedApi = vi.mocked(api);

const session: Session = {
    id: 'long', title: 'Trail notes', model: 'qwen', agent_type: 'gaia',
    created_at: '2026-09-11T00:00:00Z', updated_at: '2026-09-11T00:00:00Z',
    system_prompt: null, message_count: 4, document_ids: [],
};

const messages: Message[] = [
    { id: 1, session_id: session.id, role: 'user', content: 'What did the crew find?', created_at: session.created_at, rag_sources: null },
    { id: 2, session_id: session.id, role: 'assistant', content: 'Two **missing** signs.', created_at: session.created_at, rag_sources: null },
    { id: 3, session_id: session.id, role: 'user', content: 'And the budget?', created_at: session.created_at, rag_sources: null },
    { id: 4, session_id: session.id, role: 'assistant', content: 'Under plan by 4%.', created_at: session.created_at, rag_sources: null },
];

beforeEach(() => {
    renders.clear();
    localStorage.clear();
    mockedApi.getMessages.mockResolvedValue({ messages, total: messages.length });
    mockedApi.getMessageCount.mockResolvedValue(messages.length);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: session.id, mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        sessions: [session], currentSessionId: session.id,
        messages: [], documents: [], isStreaming: false, streamingContent: '', agentSteps: [], cards: [],
        isLoadingMessages: false, pendingPrompt: null, systemStatus: null, runningSessionIds: [],
    });
});

afterEach(() => {
    vi.clearAllMocks();
});

async function openChat() {
    render(<ChatView sessionId={session.id} />);
    await waitFor(() => expect(screen.getByText('Under plan by 4%.')).toBeInTheDocument());
    await act(async () => {});
}

const finishedRenders = () => [1, 2, 3, 4].map((id) => renders.get(id) ?? 0);

describe('a long chat redraws only what changed', () => {
    it('leaves finished messages alone while a reply streams', async () => {
        await openChat();
        act(() => useChatStore.setState({ isStreaming: true, streamingContent: '' }));
        const before = finishedRenders();

        for (const text of ['The', 'The crew', 'The crew walked', 'The crew walked the ridge']) {
            act(() => useChatStore.setState({ streamingContent: text }));
        }

        expect(screen.getByText('The crew walked the ridge')).toBeInTheDocument();
        expect(finishedRenders()).toEqual(before);
        expect(renders.get(-1)).toBeGreaterThanOrEqual(4);
    });

    it('leaves the messages alone while the user types', async () => {
        await openChat();
        const before = finishedRenders();
        const box = screen.getByRole('textbox');

        for (const text of ['h', 'he', 'hel', 'hell', 'hello']) fireEvent.change(box, { target: { value: text } });

        expect(box).toHaveValue('hello');
        expect(finishedRenders()).toEqual(before);
    });
});
