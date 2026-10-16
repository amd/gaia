// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A message typed while GAIA answers is shown, offered to the running reply, and otherwise answered next. */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView, QUEUE_BUSY_WAIT_MS } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-q',
    title: 'Queue chat',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'gaia',
};

let streams: api.StreamCallbacks[] = [];
const current = () => streams[streams.length - 1];

beforeEach(() => {
    vi.clearAllMocks();
    streams = [];
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.cancelStream.mockResolvedValue({ cancelled: true });
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        streams.push(cbs as api.StreamCallbacks);
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

async function type(text: string) {
    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: text } });
        fireEvent.keyDown(screen.getByLabelText('Message'), { key: 'Enter' });
    });
}

async function startTurn() {
    render(<ChatView sessionId={SESSION.id} />);
    await act(async () => {});  // the history load, which replaces the message list
    await type('first question');
    expect(streams).toHaveLength(1);
}

const userBubbles = () => useChatStore.getState().messages.filter((m) => m.role === 'user').map((m) => m.content);

describe('messages typed during a reply', () => {
    it('go to the running reply and show in the chat once it reads them', async () => {
        mockedApi.sendFollowUp.mockResolvedValue({ delivered: true });
        await startTurn();

        await type('also check the totals');
        expect(mockedApi.sendFollowUp).toHaveBeenCalledWith(SESSION.id, 'also check the totals');
        expect(screen.getByText(/GAIA reads it at its next step/)).toBeTruthy();
        expect(screen.getByLabelText('Message')).toHaveProperty('value', '');

        act(() => {
            current().onAgentEvent({ type: 'followup_taken', text: 'also check the totals' } as StreamEvent);
        });
        expect(userBubbles()).toEqual(['first question', 'also check the totals']);
        expect(screen.queryByLabelText('Messages waiting to send')).toBeNull();
        expect(streams).toHaveLength(1);
    });

    it('wait in a visible queue when the reply cannot take them, and are answered next, in order', async () => {
        mockedApi.sendFollowUp.mockRejectedValue(new Error('409'));
        await startTurn();

        await type('second');
        await type('third');
        expect(screen.getAllByText('Queued. Sends when this reply ends.')).toHaveLength(2);

        await act(async () => {
            current().onDone({ type: 'done', content: 'answer one', message_id: 2 } as StreamEvent);
        });
        expect(streams).toHaveLength(2);
        expect(mockedApi.sendMessageStream.mock.calls[1][1]).toBe('second');
        expect(screen.getAllByText('Queued. Sends when this reply ends.')).toHaveLength(1);

        await act(async () => {
            current().onDone({ type: 'done', content: 'answer two', message_id: 4 } as StreamEvent);
        });
        expect(mockedApi.sendMessageStream.mock.calls[2][1]).toBe('third');
    });

    it('are sent next when the reply ended before reading them', async () => {
        mockedApi.sendFollowUp.mockResolvedValue({ delivered: true });
        await startTurn();
        await type('late addition');

        await act(async () => {
            current().onAgentEvent({ type: 'followup_returned', texts: ['late addition'] } as StreamEvent);
            current().onDone({ type: 'done', content: 'answer', message_id: 2 } as StreamEvent);
        });
        expect(mockedApi.sendMessageStream.mock.calls[1][1]).toBe('late addition');
    });

    it('go back into the composer when the user stops the reply', async () => {
        mockedApi.sendFollowUp.mockRejectedValue(new Error('409'));
        await startTurn();
        await type('never mind this');

        await act(async () => {
            fireEvent.click(screen.getByLabelText('Stop'));
        });
        await act(async () => {
            current().onDone({ type: 'done', content: 'partial', message_id: 2, cancelled: true } as StreamEvent);
        });
        expect(streams).toHaveLength(1);
        expect(screen.getByLabelText('Message')).toHaveProperty('value', 'never mind this');
        expect(screen.queryByLabelText('Messages waiting to send')).toBeNull();
    });

    it('wait and try again when the chat is still busy, instead of turning into an error', async () => {
        mockedApi.sendFollowUp.mockRejectedValue(new Error('409'));
        await startTurn();
        await type('next one');
        await act(async () => {
            current().onDone({ type: 'done', content: 'answer', message_id: 2 } as StreamEvent);
        });
        expect(mockedApi.sendMessageStream.mock.calls[1][1]).toBe('next one');

        vi.useFakeTimers();
        try {
            await act(async () => {
                // The module is automocked, so its constructor sets nothing.
                current().onError(Object.assign(new api.ApiError('HTTP 409: busy', 409, 'busy', null), { httpStatus: 409 }));
            });
            expect(userBubbles()).toEqual(['first question']);
            expect(screen.getByText('Queued. Sends when this reply ends.')).toBeTruthy();
            expect(streams).toHaveLength(2);

            await act(async () => { vi.advanceTimersByTime(QUEUE_BUSY_WAIT_MS); });
            expect(streams).toHaveLength(3);
            expect(mockedApi.sendMessageStream.mock.calls[2][1]).toBe('next one');
        } finally {
            vi.useRealTimers();
        }
    });
});
