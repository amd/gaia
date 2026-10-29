// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Sends each chat's queued messages when that chat's turn ends, whether or not
 * the chat is open (#5241).
 *
 * An open chat sends through its own view, so the answer streams in front of
 * the user. Any other chat sends in the background; its view re-attaches to
 * the run when opened. Every send goes through `dispatchNext`, which allows one
 * at a time per chat, so a turn's end and a view's own send can never both send
 * the same message.
 */

import * as api from './api';
import type { StreamEvent } from '../types';
import { wakeActiveRunsPoll } from './activeRuns';
import { useChatStore } from '../stores/chatStore';
import * as messageQueue from '../utils/messageQueue';
import { describeStreamError, isStreamInterrupted } from '../utils/streamErrors';
import { log } from '../utils/logger';

/** How long a queued message waits after finding its chat busy before it is sent again. */
export const QUEUE_BUSY_WAIT_MS = 3_000;

export type SendOutcome =
    | { kind: 'accepted' }
    | { kind: 'busy' }
    | { kind: 'failed'; error: string };

/** Starts `text` as the chat's next turn; resolves once the server took it, or didn't. */
export type QueuedSend = (sessionId: string, text: string) => Promise<SendOutcome>;

const inFlight = new Set<string>();
/** When each chat last started a turn, or a view left it mid-turn. */
const lastActivity = new Map<string, number>();
/** Chats with an open view; that view sends their queue. */
const views = new Set<string>();
let wake: (() => void) | null = null;

/** Note that the chat may be busy now, so only a poll sent after this can call it idle. */
export function markActivity(sessionId: string, at = Date.now()): void {
    lastActivity.set(sessionId, at);
}

/** The server reported the chat idle, in a poll sent after its last known activity. */
export function idleByPoll(sessionId: string): boolean {
    const s = useChatStore.getState();
    return s.activeRunsAt > 0
        && s.activeRunsAt >= (lastActivity.get(sessionId) ?? 0)
        && !s.runningSessionIds.includes(sessionId)
        && !s.queuedSessionIds.includes(sessionId);
}

/** An open view takes over sending this chat's queue until the returned release is called. */
export function claimView(sessionId: string): () => void {
    views.add(sessionId);
    return () => {
        views.delete(sessionId);
        wake?.();
    };
}

function errorText(err: unknown): string {
    if (err instanceof Error) return describeStreamError(err) ?? (err.message || 'Unknown error');
    return String(err);
}

function httpStatus(err: unknown): number | undefined {
    const status = (err as { httpStatus?: unknown } | null)?.httpStatus;
    return typeof status === 'number' ? status : undefined;
}

/** A server answer meaning "not now": another turn holds the chat, or the model. */
export function isBusyError(err: unknown): boolean {
    const status = httpStatus(err);
    return status === 409 || status === 429;
}

function hold(sessionId: string): void {
    useChatStore.getState().setQueueHold(sessionId, true);
    setTimeout(() => useChatStore.getState().setQueueHold(sessionId, false), QUEUE_BUSY_WAIT_MS);
}

/**
 * A direct send this chat refused as busy (409) joins its queue and is tried
 * again shortly. Returns false for any other error, which the caller reports.
 * A 429 (the model is busy with another chat) keeps its own explanation.
 */
export function requeueRefused(sessionId: string, text: string, err: unknown): boolean {
    if (httpStatus(err) !== 409) return false;
    log.chat.info(`Chat ${sessionId} was busy; queueing the message to try again`);
    useChatStore.getState().updateQueue(sessionId, (q) => messageQueue.enqueue(q, text, 'queued'));
    hold(sessionId);
    return true;
}

/**
 * Send the chat's next queued message as its own turn, if it has one and
 * nothing for it is in flight. The caller has already decided the chat's turn
 * is over. Messages offered to that turn are settled against the server's
 * record first, so none is dropped or sent twice.
 */
export async function dispatchNext(sessionId: string, send: QueuedSend, canSend: () => boolean): Promise<void> {
    const store = useChatStore.getState;
    if (inFlight.has(sessionId) || store().queueHolds[sessionId]) return;
    const items = store().messageQueues[sessionId] ?? [];
    if (items.some((m) => m.state === 'sending')) return;
    const mustAsk = messageQueue.needsOutcome(items);
    if (!mustAsk && !messageQueue.nextToSend(items)) return;

    inFlight.add(sessionId);
    try {
        if (mustAsk) {
            let outcome: messageQueue.FollowUpOutcome;
            try {
                outcome = await api.getFollowUpOutcome(sessionId);
            } catch (err) {
                if (httpStatus(err) === 409) {
                    log.chat.info(`Chat ${sessionId} is answering again; its queue waits for that turn to end`);
                    return;
                }
                log.chat.error(`Could not learn what chat ${sessionId}'s last reply did with the messages added to it`, err);
                store().updateQueue(sessionId, (q) => messageQueue.failDelivered(
                    q, `GAIA could not say whether it read this message: ${errorText(err)}`,
                ));
                return;
            }
            store().updateQueue(sessionId, (q) => messageQueue.reconcile(q, outcome));
        }

        const next = messageQueue.nextToSend(store().messageQueues[sessionId] ?? []);
        if (!next || !canSend()) return;
        store().updateQueue(sessionId, (q) => messageQueue.promote(q, next.id, 'queued', 'starting'));
        let result: SendOutcome;
        try {
            result = await send(sessionId, next.text);
        } catch (err) {
            result = { kind: 'failed', error: errorText(err) };
        }
        if (result.kind === 'accepted') {
            markActivity(sessionId);
            store().updateQueue(sessionId, (q) => messageQueue.remove(q, next.id));
            wakeActiveRunsPoll();
            return;
        }
        if (result.kind === 'busy') {
            log.chat.info(`Queued message found chat ${sessionId} busy; holding it`);
            store().updateQueue(sessionId, (q) => messageQueue.notStarted(q, next.id));
            hold(sessionId);
            return;
        }
        log.chat.error(`Queued message for chat ${sessionId} was not sent: ${result.error}`);
        store().updateQueue(sessionId, (q) => messageQueue.notStarted(q, next.id, result.error));
    } finally {
        inFlight.delete(sessionId);
    }
}

// The synthetic "waiting to start" notice comes from the client, not the server.
function fromServer(event: StreamEvent): boolean {
    return !(event.type === 'status' && event.phase === 'waiting');
}

/**
 * Start a turn with no view watching. Once the server has it the connection is
 * dropped: the run carries on server-side and the chat's view attaches to it.
 */
export const sendInBackground: QueuedSend = (sessionId, text) => new Promise((resolve) => {
    let settled = false;
    let controller: AbortController | null = null;
    let acceptedEarly = false;
    const settle = (outcome: SendOutcome) => {
        if (settled) return;
        settled = true;
        if (outcome.kind === 'accepted') {
            if (controller) controller.abort();
            else acceptedEarly = true;
        }
        resolve(outcome);
    };
    const accepted = () => settle({ kind: 'accepted' });
    log.chat.info(`Sending queued message for chat ${sessionId} in the background`);
    controller = api.sendMessageStream(sessionId, text, {
        onChunk: accepted,
        onAgentEvent: (event) => { if (fromServer(event)) accepted(); },
        onDone: accepted,
        onError: (err) => {
            if (isStreamInterrupted(err)) accepted();
            else if (isBusyError(err)) settle({ kind: 'busy' });
            else settle({ kind: 'failed', error: errorText(err) });
        },
    });
    if (acceptedEarly) controller.abort();
});

/** Send queued messages for chats with no open view, as each one's turn ends. Returns its stop. */
export function startQueueDispatcher(send: QueuedSend = sendInBackground): () => void {
    const run = () => {
        const s = useChatStore.getState();
        if (s.systemStatus?.init_state === 'initializing') return;
        for (const sessionId of Object.keys(s.messageQueues)) {
            if (views.has(sessionId) || !idleByPoll(sessionId)) continue;
            void dispatchNext(sessionId, send, () => !views.has(sessionId));
        }
    };
    const unsubscribe = useChatStore.subscribe((s, prev) => {
        if (
            s.messageQueues !== prev.messageQueues
            || s.queueHolds !== prev.queueHolds
            || s.activeRunsAt !== prev.activeRunsAt
            || s.runningSessionIds !== prev.runningSessionIds
            || s.systemStatus !== prev.systemStatus
        ) {
            run();
        }
    });
    wake = run;
    run();
    return () => {
        unsubscribe();
        if (wake === run) wake = null;
    };
}

/** Test seam: forget every in-flight send, view and timestamp. */
export function resetQueueDispatch(): void {
    inFlight.clear();
    lastActivity.clear();
    views.clear();
    wake = null;
}
