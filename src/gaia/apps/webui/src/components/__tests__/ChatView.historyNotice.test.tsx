// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A turn that pushes older turns out of the model's view says so, once, inline. */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);
const NOTICE = "Older messages are now out of the model's view (6 turns). Ask me to recall them if you need them.";

const SESSION: Session = {
    id: 'session-1',
    title: 'Existing chat',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'gaia',
};

let callbacks: api.StreamCallbacks | null = null;

beforeEach(() => {
    vi.clearAllMocks();
    callbacks = null;
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        callbacks = cbs as api.StreamCallbacks;
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        sessions: [SESSION],
        currentSessionId: SESSION.id,
        messages: [],
        documents: [],
        isStreaming: false,
        streamingContent: '',
        agentSteps: [],
        cards: [],
        isLoadingMessages: false,
        pendingPrompt: null,
        systemStatus: null,
    });
});

async function send() {
    render(<ChatView sessionId={SESSION.id} />);
    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'next question' } });
        fireEvent.click(screen.getByLabelText('Send'));
    });
    expect(callbacks).not.toBeNull();
}

function notes() {
    return Array.from(document.querySelectorAll('.history-notice')).map((n) => n.textContent);
}

describe('ChatView history notice', () => {
    it('shows one inline note while streaming and keeps it on the finished turn', async () => {
        await send();
        act(() => {
            callbacks!.onAgentEvent({
                type: 'status', status: 'history_cut', message: NOTICE, turns: 6, tokens: 600,
            } as unknown as StreamEvent);
        });
        expect(notes()).toEqual([NOTICE]);
        expect(useChatStore.getState().agentSteps).toEqual([]);

        await act(async () => {
            callbacks!.onDone({ type: 'done', content: 'answer', message_id: 7 } as StreamEvent);
        });
        expect(notes()).toEqual([NOTICE]);
        const { messages } = useChatStore.getState();
        expect(messages[messages.length - 1]?.historyNotice).toBe(NOTICE);

        // A refetch replaces the messages with server copies that carry no notice.
        act(() => {
            useChatStore.setState({
                messages: messages.map(({ historyNotice: _drop, ...m }) => m),
            });
        });
        expect(notes()).toEqual([NOTICE]);
    });

    it('shows nothing when no cut happened', async () => {
        await send();
        await act(async () => {
            callbacks!.onDone({ type: 'done', content: 'answer', message_id: 8 } as StreamEvent);
        });
        expect(notes()).toEqual([]);
    });
});
