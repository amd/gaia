// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Messages queued in a chat go out when that chat's turn ends, even while
 * another chat is open, and are never dropped or sent twice (#5241).
 */

import { act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import { useChatStore } from '../../stores/chatStore';
import * as messageQueue from '../../utils/messageQueue';
import type { QueuedMessage } from '../../utils/messageQueue';
import type { StreamEvent } from '../../types';
import {
    QUEUE_BUSY_WAIT_MS,
    claimView,
    dispatchNext,
    markActivity,
    resetQueueDispatch,
    sendInBackground,
    startQueueDispatcher,
    type QueuedSend,
    type SendOutcome,
} from '../queueDispatch';

vi.mock('../api');
const mockedApi = vi.mocked(api);

const A = 'chat-a';
const B = 'chat-b';

/** A sender whose sends the test settles by hand, in order. */
function manualSender() {
    const calls: { sessionId: string; text: string; settle: (o: SendOutcome) => void }[] = [];
    const send: QueuedSend = (sessionId, text) => new Promise((resolve) => {
        calls.push({ sessionId, text, settle: resolve });
    });
    return { send, calls };
}

function queue(sessionId: string, ...items: [string, QueuedMessage['state']][]) {
    useChatStore.getState().updateQueue(sessionId, (q) => items.reduce((acc, [t, st]) => messageQueue.enqueue(acc, t, st), q));
}

const states = (sessionId: string) =>
    (useChatStore.getState().messageQueues[sessionId] ?? []).map((m) => [m.text, m.state]);

/** A poll of GET /api/chat/active landing now. */
function poll(running: string[] = [], at = Date.now() + 1) {
    useChatStore.getState().setActiveRuns({ session_ids: running, queued: [], awaiting_user: {} }, at);
}

const flush = () => act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); });

let stop: (() => void) | null = null;

beforeEach(() => {
    vi.clearAllMocks();
    resetQueueDispatch();
    useChatStore.setState({
        messageQueues: {},
        queueHolds: {},
        runningSessionIds: [],
        queuedSessionIds: [],
        activeRunsAt: 0,
        currentSessionId: null,
        systemStatus: null,
        sessions: [],
    });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
});

afterEach(() => {
    stop?.();
    stop = null;
    vi.useRealTimers();
});

describe('a chat left while it was answering', () => {
    it('sends its queue, in order, once that chat\'s turn ends', async () => {
        const { send, calls } = manualSender();
        markActivity(A, 1_000);
        poll([A], 1_001);
        queue(A, ['first', 'queued'], ['second', 'queued']);
        stop = startQueueDispatcher(send);

        expect(calls).toHaveLength(0);  // still answering

        poll([], 2_000);
        expect(calls.map((c) => c.text)).toEqual(['first']);
        // The message going out stays in the store until the server takes it.
        expect(states(A)).toEqual([['first', 'starting'], ['second', 'queued']]);

        await act(async () => { calls[0].settle({ kind: 'accepted' }); });
        expect(states(A)).toEqual([['second', 'queued']]);
        // Its own turn is running now; a poll from before it started can't call the chat idle.
        poll([], 1_500);
        expect(calls).toHaveLength(1);

        poll([A], Date.now() + 10);
        poll([], Date.now() + 20);
        expect(calls.map((c) => c.text)).toEqual(['first', 'second']);
    });

    it('waits for the first poll after a reload before sending anything', () => {
        const { send, calls } = manualSender();
        queue(A, ['restored', 'queued']);
        stop = startQueueDispatcher(send);
        expect(calls).toHaveLength(0);
        poll([], 5);
        expect(calls.map((c) => c.text)).toEqual(['restored']);
    });

    it('keeps two chats\' queues apart, each in its own order', async () => {
        const { send, calls } = manualSender();
        queue(A, ['a1', 'queued'], ['a2', 'queued']);
        queue(B, ['b1', 'queued'], ['b2', 'queued']);
        stop = startQueueDispatcher(send);
        poll([], 10);

        const sent = (from = 0) => calls.slice(from).map((c) => `${c.sessionId}:${c.text}`).sort();
        expect(sent()).toEqual([`${A}:a1`, `${B}:b1`]);
        await act(async () => { calls[1].settle({ kind: 'accepted' }); calls[0].settle({ kind: 'accepted' }); });
        expect(states(A)).toEqual([['a2', 'queued']]);
        expect(states(B)).toEqual([['b2', 'queued']]);

        poll([], Date.now() + 10);
        expect(sent(2)).toEqual([`${A}:a2`, `${B}:b2`]);
    });

    it('leaves an open chat to its own view, and takes over when the view closes', async () => {
        const { send, calls } = manualSender();
        const release = claimView(A);
        queue(A, ['mine', 'queued']);
        stop = startQueueDispatcher(send);
        poll([], 10);
        expect(calls).toHaveLength(0);

        release();
        expect(calls.map((c) => c.text)).toEqual(['mine']);
    });
});

describe('a message added to a reply that ended with nobody watching', () => {
    it('goes out next if the reply never read it, and is not resent if it did', async () => {
        const { send, calls } = manualSender();
        mockedApi.getFollowUpOutcome.mockResolvedValue({ taken: ['was read'], returned: ['came back'] });
        queue(A, ['was read', 'delivered'], ['came back', 'delivered'], ['typed later', 'queued']);
        stop = startQueueDispatcher(send);

        poll([], 10);
        await flush();

        expect(mockedApi.getFollowUpOutcome).toHaveBeenCalledWith(A);
        expect(calls.map((c) => c.text)).toEqual(['came back']);
        expect(states(A)).toEqual([['came back', 'starting'], ['typed later', 'queued']]);
    });

    it('is shown as failed, never dropped, when the server has no record of it', async () => {
        const { send, calls } = manualSender();
        mockedApi.getFollowUpOutcome.mockResolvedValue({ taken: [], returned: [] });
        queue(A, ['lost?', 'delivered'], ['after', 'queued']);
        stop = startQueueDispatcher(send);

        poll([], 10);
        await flush();

        expect(states(A)).toEqual([['lost?', 'failed'], ['after', 'queued']]);
        // Order is kept: nothing jumps ahead of the failed message.
        expect(calls).toHaveLength(0);
    });

    it('is shown as failed when the record cannot be read', async () => {
        mockedApi.getFollowUpOutcome.mockRejectedValue(Object.assign(new Error('HTTP 500'), { httpStatus: 500 }));
        queue(A, ['d', 'delivered']);
        await dispatchNext(A, manualSender().send, () => true);
        expect(states(A)).toEqual([['d', 'failed']]);
        expect(useChatStore.getState().messageQueues[A][0].error).toContain('HTTP 500');
    });

    it('stays as it is when the chat turns out to be answering again', async () => {
        mockedApi.getFollowUpOutcome.mockRejectedValue(Object.assign(new Error('busy'), { httpStatus: 409 }));
        queue(A, ['d', 'delivered']);
        await dispatchNext(A, manualSender().send, () => true);
        expect(states(A)).toEqual([['d', 'delivered']]);
    });

    it('waits while its offer to the reply is still in flight', async () => {
        const { send, calls } = manualSender();
        queue(A, ['offering', 'sending'], ['next', 'queued']);
        await dispatchNext(A, send, () => true);
        expect(mockedApi.getFollowUpOutcome).not.toHaveBeenCalled();
        expect(calls).toHaveLength(0);
    });
});

describe('a queued send that does not go through', () => {
    it('goes back to the front and tries again after a wait when the chat is busy', async () => {
        vi.useFakeTimers();
        const { send, calls } = manualSender();
        queue(A, ['first', 'queued'], ['second', 'queued']);
        stop = startQueueDispatcher(send);
        poll([], 10);

        await act(async () => { calls[0].settle({ kind: 'busy' }); });
        expect(states(A)).toEqual([['first', 'queued'], ['second', 'queued']]);
        expect(calls).toHaveLength(1);

        await act(async () => { vi.advanceTimersByTime(QUEUE_BUSY_WAIT_MS); });
        expect(calls.map((c) => c.text)).toEqual(['first', 'first']);
    });

    it('is shown as failed with the reason, holds the rest, and goes once the user retries', async () => {
        const { send, calls } = manualSender();
        queue(A, ['first', 'queued'], ['second', 'queued']);
        stop = startQueueDispatcher(send);
        poll([], 10);

        await act(async () => { calls[0].settle({ kind: 'failed', error: 'HTTP 500' }); });
        expect(states(A)).toEqual([['first', 'failed'], ['second', 'queued']]);
        expect(useChatStore.getState().messageQueues[A][0].error).toBe('HTTP 500');
        expect(calls).toHaveLength(1);

        const failed = useChatStore.getState().messageQueues[A][0];
        useChatStore.getState().updateQueue(A, (q) => messageQueue.promote(q, failed.id, 'failed', 'queued'));
        expect(calls.map((c) => c.text)).toEqual(['first', 'first']);
    });

    it('is shown as failed when the sender itself throws', async () => {
        queue(A, ['boom', 'queued']);
        await dispatchNext(A, () => Promise.reject(new Error('kaboom')), () => true);
        expect(states(A)).toEqual([['boom', 'failed']]);
    });
});

describe('never sending a message twice', () => {
    it('lets only one of two racing senders send', async () => {
        const first = manualSender();
        const second = manualSender();
        queue(A, ['once', 'queued']);

        const one = dispatchNext(A, first.send, () => true);
        const two = dispatchNext(A, second.send, () => true);
        first.calls[0].settle({ kind: 'accepted' });
        await Promise.all([one, two]);

        expect(first.calls.length + second.calls.length).toBe(1);
        expect(states(A)).toEqual([]);
    });

    it('does not send while the message ahead is still going out', async () => {
        const { send, calls } = manualSender();
        queue(A, ['first', 'queued'], ['second', 'queued']);
        void dispatchNext(A, send, () => true);
        resetQueueDispatch();  // even if the in-flight lock were lost
        await dispatchNext(A, send, () => true);
        expect(calls.map((c) => c.text)).toEqual(['first']);
    });

    it('sends nothing when the view can no longer send after asking the server', async () => {
        const { send, calls } = manualSender();
        mockedApi.getFollowUpOutcome.mockResolvedValue({ taken: [], returned: ['back'] });
        queue(A, ['back', 'delivered']);
        await dispatchNext(A, send, () => false);
        expect(calls).toHaveLength(0);
        expect(states(A)).toEqual([['back', 'queued']]);
    });
});

describe('a deleted chat', () => {
    it('takes its queue with it, and nothing is sent for it', () => {
        const { send, calls } = manualSender();
        queue(A, ['orphan', 'queued']);
        useChatStore.getState().removeSession(A);
        stop = startQueueDispatcher(send);
        poll([], 10);
        expect(calls).toHaveLength(0);
        expect(useChatStore.getState().messageQueues[A]).toBeUndefined();
    });

    it('does not bring a message back when its send fails after the chat was deleted', async () => {
        const { send, calls } = manualSender();
        queue(A, ['in flight', 'queued']);
        const done = dispatchNext(A, send, () => true);
        useChatStore.getState().removeSession(A);
        calls[0].settle({ kind: 'failed', error: 'Session not found' });
        await done;
        expect(useChatStore.getState().messageQueues[A]).toBeUndefined();
    });
});

describe('sending in the background', () => {
    let callbacks: api.StreamCallbacks;
    let controller: AbortController;

    beforeEach(() => {
        mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
            callbacks = cbs as api.StreamCallbacks;
            controller = new AbortController();
            return controller;
        });
    });

    it('counts as sent once the server answers, then lets the run go on without it', async () => {
        const outcome = sendInBackground(A, 'hello');
        // The client's own "waiting" notice is not the server taking it.
        callbacks.onAgentEvent({ type: 'status', phase: 'waiting', message: api.SEND_WAITING_NOTICE_TEXT } as StreamEvent);
        expect(controller.signal.aborted).toBe(false);

        callbacks.onAgentEvent({ type: 'status', phase: 'queued' } as StreamEvent);
        await expect(outcome).resolves.toEqual({ kind: 'accepted' });
        expect(controller.signal.aborted).toBe(true);
        expect(mockedApi.sendMessageStream).toHaveBeenCalledWith(A, 'hello', expect.anything());
    });

    it('reports a busy chat or model as busy', async () => {
        const conflict = sendInBackground(A, 'x');
        callbacks.onError(Object.assign(new Error('busy'), { httpStatus: 409 }));
        await expect(conflict).resolves.toEqual({ kind: 'busy' });

        const model = sendInBackground(A, 'y');
        callbacks.onError(Object.assign(new Error('model busy'), { httpStatus: 429 }));
        await expect(model).resolves.toEqual({ kind: 'busy' });
    });

    it('reports any other refusal as failed, with the reason', async () => {
        const outcome = sendInBackground(A, 'x');
        callbacks.onError(new Error('Failed to fetch'));
        await expect(outcome).resolves.toEqual({ kind: 'failed', error: 'Failed to fetch' });
    });

    it('counts a connection cut after the run started as sent', async () => {
        const outcome = sendInBackground(A, 'x');
        const dropped = new Error('cut');
        dropped.name = 'StreamDroppedError';
        callbacks.onError(dropped);
        await expect(outcome).resolves.toEqual({ kind: 'accepted' });
    });
});

describe('after a reload', () => {
    it('keeps every chat\'s queue', async () => {
        queue(A, ['survives', 'queued']);
        queue(B, ['offered', 'sending']);
        const saved = localStorage.getItem('gaia-chat-message-queues');
        expect(saved).toBeTruthy();

        vi.resetModules();
        const { useChatStore: fresh } = await import('../../stores/chatStore');
        const restored = fresh.getState().messageQueues;
        expect(restored[A].map((m) => [m.text, m.state])).toEqual([['survives', 'queued']]);
        // Whether the reply got it is unknown, so the server is asked once the turn is over.
        expect(restored[B].map((m) => [m.text, m.state])).toEqual([['offered', 'delivered']]);
    });

    it('forgets a chat\'s queue once it empties', () => {
        queue(A, ['only', 'queued']);
        const [only] = useChatStore.getState().messageQueues[A];
        useChatStore.getState().updateQueue(A, (q) => messageQueue.remove(q, only.id));
        expect(JSON.parse(localStorage.getItem('gaia-chat-message-queues') ?? '{}')).toEqual({});
    });
});
