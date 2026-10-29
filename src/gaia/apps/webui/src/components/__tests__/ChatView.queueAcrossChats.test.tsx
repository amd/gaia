// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Messages queued in a chat survive switching to another chat, and go out when that chat's turn ends (#5241). */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import * as messageQueue from '../../utils/messageQueue';
import type { Session, StreamEvent } from '../../types';
import * as api from '../../services/api';
import { resetQueueDispatch, startQueueDispatcher } from '../../services/queueDispatch';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const session = (id: string): Session => ({
    id,
    title: `Chat ${id}`,
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 1,
    document_ids: [],
    agent_type: 'gaia',
});
const A = 'chat-a';
const B = 'chat-b';

interface Stream { sessionId: string; text: string | null; cbs: api.StreamCallbacks; controller: AbortController }
let streams: Stream[] = [];
let running: string[] = [];
let stopDispatcher: (() => void) | null = null;

const sends = (sessionId = A) => streams.filter((s) => s.sessionId === sessionId && s.text !== null).map((s) => s.text);
const last = () => streams[streams.length - 1];
const queueOf = (sessionId: string) =>
    (useChatStore.getState().messageQueues[sessionId] ?? []).map((m) => [m.text, m.state]);

/** A poll of GET /api/chat/active landing now. */
async function poll(ids: string[]) {
    running = ids;
    await act(async () => {
        useChatStore.getState().setActiveRuns({ session_ids: ids, queued: [], awaiting_user: {} }, Date.now() + 1_000);
    });
}

beforeEach(() => {
    vi.clearAllMocks();
    streams = [];
    running = [];
    resetQueueDispatch();
    mockedApi.getMessages.mockImplementation(async () => {
        const { messages } = useChatStore.getState();
        return { messages, total: messages.length };
    });
    mockedApi.getMessageCount.mockImplementation(async () => useChatStore.getState().messages.length);
    mockedApi.getActiveRuns.mockImplementation(async () => ({ session_ids: running, queued: [], awaiting_user: {} }));
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.cancelStream.mockResolvedValue({ cancelled: true });
    mockedApi.sendMessageStream.mockImplementation((sid, msg, cbs) => {
        const controller = new AbortController();
        streams.push({ sessionId: sid, text: msg, cbs: cbs as api.StreamCallbacks, controller });
        return controller;
    });
    mockedApi.attachToRun.mockImplementation((sid, cbs) => {
        const controller = new AbortController();
        streams.push({ sessionId: sid, text: null, cbs, controller });
        return controller;
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        sessions: [session(A), session(B)],
        currentSessionId: A,
        messages: [],
        documents: [],
        isStreaming: false,
        streamingContent: '',
        agentSteps: [],
        cards: [],
        isLoadingMessages: false,
        pendingPrompt: null,
        systemStatus: null,
        messageQueues: {},
        queueHolds: {},
        runningSessionIds: [],
        queuedSessionIds: [],
        activeRunsAt: 0,
    });
});

afterEach(() => {
    stopDispatcher?.();
    stopDispatcher = null;
    vi.useRealTimers();
});

async function type(text: string) {
    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: text } });
        fireEvent.keyDown(screen.getByLabelText('Message'), { key: 'Enter' });
    });
}

/** Open chat A, ask a question, and queue `queued` while it answers. */
async function answerWithQueue(...queued: string[]) {
    const view = render(<ChatView sessionId={A} />);
    await act(async () => {});
    await type('first question');
    // The server took it and the turn is running.
    await act(async () => { last().cbs.onAgentEvent({ type: 'status', phase: 'working' } as StreamEvent); });
    running = [A];
    for (const text of queued) await type(text);
    return view;
}

/** Leave chat A for chat B, as the sidebar does. */
async function switchAway(view: ReturnType<typeof render>) {
    await act(async () => {
        useChatStore.setState({ currentSessionId: B });
        view.unmount();
        useChatStore.getState().resetStreaming();
    });
}

describe('leaving a chat while it answers', () => {
    it('sends its queued messages, in order, when its turn ends while another chat is open', async () => {
        const view = await answerWithQueue('q1', 'q2');
        await switchAway(view);
        expect(queueOf(A)).toEqual([['q1', 'queued'], ['q2', 'queued']]);

        stopDispatcher = startQueueDispatcher();
        await poll([A]);
        expect(sends()).toEqual(['first question']);

        await poll([]);
        expect(sends()).toEqual(['first question', 'q1']);
        // The server took it; the run goes on with nobody watching, and q2 waits for it.
        await act(async () => { last().cbs.onAgentEvent({ type: 'status', phase: 'queued' } as StreamEvent); });
        expect(last().controller.signal.aborted).toBe(true);
        expect(queueOf(A)).toEqual([['q2', 'queued']]);

        await poll([A]);
        await poll([]);
        expect(sends()).toEqual(['first question', 'q1', 'q2']);
        expect(sends(B)).toEqual([]);
    });

    it('keeps them when the user comes back before the turn ends, and sends each once', async () => {
        const view = await answerWithQueue('q1');
        await switchAway(view);
        stopDispatcher = startQueueDispatcher();
        await poll([A]);

        await act(async () => { useChatStore.setState({ currentSessionId: A }); });
        render(<ChatView sessionId={A} />);
        await act(async () => {});
        await poll([A]);
        expect(screen.getByText('Queued. Sends when this reply ends.')).toBeTruthy();
        expect(screen.getByText('q1')).toBeTruthy();

        // The view re-attached to the running turn, which now ends.
        const attached = streams.find((s) => s.text === null);
        expect(attached).toBeTruthy();
        await act(async () => {
            attached!.cbs.onDone({ type: 'done', content: 'first answer', message_id: 7 } as StreamEvent);
        });
        running = [];
        await poll([]);

        expect(sends()).toEqual(['first question', 'q1']);
        expect(screen.queryByText('Queued. Sends when this reply ends.')).toBeNull();
    });

    it('survives going back and forth several times', async () => {
        let view = await answerWithQueue('q1', 'q2');
        for (let i = 0; i < 3; i++) {
            await switchAway(view);
            await act(async () => { useChatStore.setState({ currentSessionId: A }); });
            view = render(<ChatView sessionId={A} />);
            await act(async () => {});
        }
        expect(queueOf(A)).toEqual([['q1', 'queued'], ['q2', 'queued']]);
        expect(sends()).toEqual(['first question']);
    });
});

describe('a message added to the running reply', () => {
    it('goes out as its own message when the reply ends unread while another chat is open', async () => {
        mockedApi.sendFollowUp.mockResolvedValue({ delivered: true });
        mockedApi.getFollowUpOutcome.mockResolvedValue({ taken: [], returned: ['late addition'] });
        const view = await answerWithQueue('late addition');
        await act(async () => { fireEvent.click(screen.getByText('Add to this reply')); });
        expect(queueOf(A)).toEqual([['late addition', 'delivered']]);

        await switchAway(view);
        stopDispatcher = startQueueDispatcher();
        await poll([]);
        await act(async () => {});

        expect(mockedApi.getFollowUpOutcome).toHaveBeenCalledWith(A);
        expect(sends()).toEqual(['first question', 'late addition']);
    });

    it('is not sent again when the reply read it while nobody was watching', async () => {
        mockedApi.sendFollowUp.mockResolvedValue({ delivered: true });
        mockedApi.getFollowUpOutcome.mockResolvedValue({ taken: ['read it'], returned: [] });
        const view = await answerWithQueue('read it');
        await act(async () => { fireEvent.click(screen.getByText('Add to this reply')); });

        await switchAway(view);
        stopDispatcher = startQueueDispatcher();
        await poll([]);
        await act(async () => {});

        expect(sends()).toEqual(['first question']);
        expect(queueOf(A)).toEqual([]);
    });

    it('is tracked even if the reply reports on it after the user left', async () => {
        const view = await answerWithQueue('quick');
        mockedApi.sendFollowUp.mockResolvedValue({ delivered: true });
        await act(async () => { fireEvent.click(screen.getByText('Add to this reply')); });
        const stream = streams[0];
        await switchAway(view);

        act(() => {
            stream.cbs.onAgentEvent({ type: 'followup_taken', text: 'quick' } as StreamEvent);
        });
        expect(queueOf(A)).toEqual([]);
        // Nothing leaks into the chat now on screen.
        expect(useChatStore.getState().messages.filter((m) => m.content === 'quick')).toEqual([]);
    });
});

describe('a queued message whose send fails', () => {
    it('is not lost when the user leaves before the server answers, and shows as failed with a retry', async () => {
        const view = await answerWithQueue('q1');
        await act(async () => {
            streams[0].cbs.onDone({ type: 'done', content: 'answer', message_id: 2 } as StreamEvent);
        });
        running = [];
        const pending = last();
        expect(pending.text).toBe('q1');

        await switchAway(view);
        // Leaving did not cancel a send the server had not answered.
        expect(pending.controller.signal.aborted).toBe(false);
        await act(async () => {
            pending.cbs.onError(Object.assign(new api.ApiError('HTTP 500', 500, 'boom', null), { httpStatus: 500, detail: 'boom' }));
        });
        expect(queueOf(A)).toEqual([['q1', 'failed']]);

        await act(async () => { useChatStore.setState({ currentSessionId: A }); });
        render(<ChatView sessionId={A} />);
        await act(async () => {});
        expect(screen.getByText(/^Not sent\./)).toBeTruthy();

        await act(async () => { fireEvent.click(screen.getByLabelText('Send again: q1')); });
        expect(sends()).toEqual(['first question', 'q1', 'q1']);
    });

    it('is removed from the queue once the server takes it, even after the user left', async () => {
        const view = await answerWithQueue('q1');
        await act(async () => {
            streams[0].cbs.onDone({ type: 'done', content: 'answer', message_id: 2 } as StreamEvent);
        });
        const pending = last();
        await switchAway(view);

        await act(async () => { pending.cbs.onAgentEvent({ type: 'status', phase: 'working' } as StreamEvent); });
        expect(queueOf(A)).toEqual([]);
        // The run carries on server-side; this view no longer listens.
        expect(pending.controller.signal.aborted).toBe(true);
    });

    it('stays shown in the open chat, not as a vanished bubble', async () => {
        await answerWithQueue('q1');
        await act(async () => {
            streams[0].cbs.onDone({ type: 'done', content: 'answer', message_id: 2 } as StreamEvent);
        });
        await act(async () => { last().cbs.onError(new Error('Failed to fetch')); });

        expect(useChatStore.getState().messages.filter((m) => m.role === 'user').map((m) => m.content))
            .toEqual(['first question']);
        expect(screen.getByText('Not sent. Failed to fetch')).toBeTruthy();
        expect(screen.getByLabelText('Send again: q1')).toBeTruthy();
    });
});

describe('order and double sends', () => {
    it('puts a message typed while others wait at the end of the line', async () => {
        useChatStore.getState().updateQueue(A, (q) => messageQueue.enqueue(q, 'stuck', 'failed'));
        render(<ChatView sessionId={A} />);
        await act(async () => {});
        await type('newer');

        expect(sends()).toEqual([]);
        expect(queueOf(A)).toEqual([['stuck', 'failed'], ['newer', 'queued']]);

        await act(async () => { fireEvent.click(screen.getByLabelText('Send again: stuck')); });
        expect(sends()).toEqual(['stuck']);
        await act(async () => { last().cbs.onDone({ type: 'done', content: 'one', message_id: 3 } as StreamEvent); });
        expect(sends()).toEqual(['stuck', 'newer']);
    });

    it('sends a queued message once when the turn end and a poll both say the chat is free', async () => {
        stopDispatcher = startQueueDispatcher();
        await answerWithQueue('only once');
        await act(async () => {
            streams[0].cbs.onDone({ type: 'done', content: 'answer', message_id: 2 } as StreamEvent);
            useChatStore.getState().setActiveRuns({ session_ids: [], queued: [], awaiting_user: {} }, Date.now() + 1_000);
        });
        await poll([]);
        expect(sends()).toEqual(['first question', 'only once']);
    });
});
