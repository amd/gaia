// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, fireEvent, act } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Session } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-gaia',
    title: 'Existing chat',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'gemma',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'gaia',
};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: SESSION.id, mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [], total: 0, total_size_bytes: 0, total_chunks: 0,
    });
    mockedApi.sendMessageStream.mockImplementation(() => new AbortController());

    useChatStore.setState({
        sessions: [SESSION],
        currentSessionId: SESSION.id,
        messages: [],
        documents: [],
        isStreaming: false,
        streamingContent: '',
        agentSteps: [],
        isLoadingMessages: false,
        pendingPrompt: null,
        systemStatus: null,
        messageQueues: {},
        queueHolds: {},
    });
});

describe('ChatView dispatch', () => {
    it('sends the session, the text and the callbacks — the backend owns the agent', async () => {
        render(<ChatView sessionId={SESSION.id} />);

        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), {
                target: { value: 'triage my inbox' },
            });
            fireEvent.click(screen.getByLabelText('Send'));
        });

        expect(mockedApi.sendMessageStream).toHaveBeenCalledTimes(1);
        const call = mockedApi.sendMessageStream.mock.calls[0];
        expect(call).toHaveLength(3);
        expect(call[0]).toBe(SESSION.id);
        expect(call[1]).toBe('triage my inbox');
        expect(typeof (call[2] as api.StreamCallbacks).onChunk).toBe('function');
    });

    it('queues a message the busy chat refused instead of showing the refusal as a reply (#5712)', async () => {
        mockedApi.sendMessageStream.mockImplementation((_sid, _text, callbacks) => {
            const busy = Object.assign(new Error('A chat request is already in progress for this session.'), { httpStatus: 409 });
            queueMicrotask(() => (callbacks as api.StreamCallbacks).onError?.(busy));
            return new AbortController();
        });
        render(<ChatView sessionId={SESSION.id} />);

        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'hello' } });
            fireEvent.click(screen.getByLabelText('Send'));
            await new Promise((r) => setTimeout(r, 0));
        });

        const { messages, messageQueues, queueHolds } = useChatStore.getState();
        expect(messages.some((m) => m.role === 'assistant')).toBe(false);
        expect(messageQueues[SESSION.id]?.map((m) => [m.text, m.state])).toEqual([['hello', 'queued']]);
        expect(queueHolds[SESSION.id]).toBe(true);
        expect(screen.getByText('GAIA is still busy in this chat. Trying again in a few seconds.')).toBeInTheDocument();
    });

    it('sends the opening message ahead of one queued while the chat was created (#5712)', async () => {
        useChatStore.setState({
            pendingPrompt: 'first',
            messageQueues: { [SESSION.id]: [{ id: 1, text: 'second', state: 'queued' }] },
        });

        await act(async () => {
            render(<ChatView sessionId={SESSION.id} />);
            await new Promise((r) => setTimeout(r, 0));
        });

        expect(mockedApi.sendMessageStream).toHaveBeenCalledTimes(1);
        expect(mockedApi.sendMessageStream.mock.calls[0][1]).toBe('first');
        expect(useChatStore.getState().messageQueues[SESSION.id]?.map((m) => m.text)).toEqual(['second']);
    });
});
