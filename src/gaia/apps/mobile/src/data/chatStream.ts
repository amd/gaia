// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { hashKey, notifyManager, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo } from 'react';
import { AppState } from 'react-native';

import { MAX_STREAM_RECONNECTS, RECONNECT_DELAYS_MS } from '@webui/utils/streamErrors.core';
import { STOP_GRACE_MS } from '@webui/utils/turnEnding.core';

import type { GaiaClient, Message, MessageList, StreamEvent } from '@/api/client.types';
import {
    asGaiaError,
    GaiaApiError,
    GaiaConfigError,
    GaiaNetworkError,
    GaiaStreamDroppedError,
    GaiaStreamError,
    GaiaStreamStalledError,
    GaiaUnreachableError,
    type GaiaError,
} from '@/api/errors';
import {
    applyEvent,
    beginTurn,
    expirePendingPrompts,
    IDLE_TURN,
    openTurn,
    readTurn,
    useChatStreamStore,
    writeTurn,
    type ChatStreamStatus,
    type ChatTurn,
    type ConfirmationRequest,
    type PermissionRequest,
    type QuestionRequest,
} from '@/state/chatStreams';

import { useGaiaClient } from './GaiaClientProvider';
import { queryKeys } from './queries';

export type { ChatStreamStatus, ConfirmationRequest, PermissionRequest, QuestionRequest } from '@/state/chatStreams';

export interface ChatStreamDeps {
    client: GaiaClient;
    queryClient: QueryClient;
}

/** This phone's view of one chat's turn. Aborting it leaves the turn running on the PC. */
interface Run {
    readonly kind: 'send' | 'attach';
    readonly view: AbortController;
    /** An event arrived, so the turn exists on the PC and a stop can reach it. */
    sawEvent: boolean;
    stopRequested: boolean;
    /** Re-attaches since the live view was last whole; 0 for a run the person started. */
    reconnects: number;
    /** Waits out the backoff before a re-attach. */
    retryTimer?: ReturnType<typeof setTimeout>;
    /** Ends the turn here when GAIA never confirms a stop. */
    graceTimer?: ReturnType<typeof setTimeout>;
    /** The id GAIA saved the reply under, from the turn's `done`. */
    replyId?: number;
    /** The stream closed and the turn only waits on its transcript, so leaving the screen must not drop it. */
    ending?: boolean;
}

const runs = new Map<string, Run>();
const subscribers = new Map<string, number>();
const foregroundListeners = new Map<string, { remove(): void }>();
/** Ended turns still showing their answer until the transcript holds GAIA's saved copy; each stops its wait. */
const awaitingReply = new Map<string, () => void>();

/** Re-reads for a saved reply missing from the transcript, after each of these waits. */
const REPLY_REREAD_DELAYS_MS = [250, 500, 1000, 2000];

function isCurrent(sessionId: string, run: Run): boolean {
    return runs.get(sessionId) === run;
}

function update(sessionId: string, run: Run, change: (turn: ChatTurn) => ChatTurn): void {
    if (isCurrent(sessionId, run)) writeTurn(sessionId, change(readTurn(sessionId)));
}

/** The run is over: nothing of it is left running on this phone. */
function finishRun(sessionId: string): void {
    clearTimeout(runs.get(sessionId)?.graceTimer);
    clearTimeout(runs.get(sessionId)?.retryTimer);
    runs.delete(sessionId);
}

function newRun(kind: Run['kind'], reconnects = 0): Run {
    return { kind, view: new AbortController(), sawEvent: false, stopRequested: false, reconnects };
}

function refreshChat(queryClient: QueryClient, sessionId: string): Promise<void> {
    void queryClient.invalidateQueries({ queryKey: queryKeys.session(sessionId) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
    return queryClient.invalidateQueries({ queryKey: queryKeys.messages(sessionId) });
}

/** The transcript holds the turn's saved reply: by its id, else as the reply after the last question. */
function holdsReply(list: MessageList | undefined, replyId: number | undefined): boolean {
    if (!list) return false;
    if (replyId !== undefined) return list.messages.some((m) => m.id === replyId);
    return list.messages.filter((m) => m.role !== 'system').at(-1)?.role === 'assistant';
}

/** Calls `landed` in the batch of each successful transcript update, with the list it brought. */
function onTranscript(queryClient: QueryClient, sessionId: string, landed: (list: MessageList | undefined) => void): () => void {
    const transcript = hashKey(queryKeys.messages(sessionId));
    return queryClient.getQueryCache().subscribe((event) => {
        // Cache events arrive inside the batch that notifies the transcript's readers, so this joins it.
        if (event.type === 'updated' && event.action.type === 'success' && event.query.queryHash === transcript) {
            const list = event.query.state.data as MessageList | undefined;
            notifyManager.schedule(() => landed(list));
        }
    });
}

/**
 * Re-reads the chat, then runs `end` in the same batch as the transcript's own
 * update, so a screen never shows the finished reply twice or not at all.
 * `end` learns whether that transcript holds the reply.
 */
async function endWithTranscript(
    queryClient: QueryClient,
    sessionId: string,
    holds: (list: MessageList | undefined) => boolean,
    end: (hasReply: boolean) => void,
): Promise<void> {
    let ended = false;
    const once = (list: MessageList | undefined) => {
        if (ended) return;
        ended = true;
        unsubscribe();
        end(holds(list));
    };
    const unsubscribe = onTranscript(queryClient, sessionId, once);
    await refreshChat(queryClient, sessionId);
    // Nothing re-read it (no screen shows it) or the read failed or was cancelled.
    notifyManager.schedule(() => once(queryClient.getQueryData<MessageList>(queryKeys.messages(sessionId))));
}

function stopAwaitingReply(sessionId: string): void {
    awaitingReply.get(sessionId)?.();
}

/**
 * The turn ended but the transcript doesn't hold its saved reply yet (a read that
 * began before GAIA saved it, or one that was cancelled): its answer stays on
 * screen, the transcript is read again a few times, and the answer gives way in
 * the batch of whichever read first brings the saved copy, however late.
 */
function awaitReply(queryClient: QueryClient, sessionId: string, holds: (list: MessageList | undefined) => boolean): void {
    stopAwaitingReply(sessionId);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const stop = () => {
        clearTimeout(timer);
        unsubscribe();
        if (awaitingReply.get(sessionId) === stop) awaitingReply.delete(sessionId);
    };
    const waiting = () => awaitingReply.get(sessionId) === stop;
    const unsubscribe = onTranscript(queryClient, sessionId, (list) => {
        if (!waiting() || !holds(list)) return;
        stop();
        writeTurn(sessionId, null);
    });
    awaitingReply.set(sessionId, stop);
    const reread = (attempt: number) => {
        const delay = REPLY_REREAD_DELAYS_MS[attempt];
        if (delay === undefined) return;
        timer = setTimeout(() => {
            void queryClient.refetchQueries({ queryKey: queryKeys.messages(sessionId), exact: true }).then(() => {
                if (waiting()) reread(attempt + 1);
            });
        }, delay);
    };
    reread(0);
}

/** The sent message, shown in the transcript until the re-read brings GAIA's own copy. */
async function showSentMessage(queryClient: QueryClient, sessionId: string, content: string): Promise<void> {
    const key = queryKeys.messages(sessionId);
    if (!queryClient.getQueryData<MessageList>(key)) return;
    // A read already in flight would land on top of it.
    await queryClient.cancelQueries({ queryKey: key });
    queryClient.setQueryData<MessageList>(key, (list) =>
        list && {
            messages: [
                ...list.messages,
                {
                    id: -Date.now(),
                    session_id: sessionId,
                    role: 'user',
                    content,
                    created_at: new Date().toISOString(),
                    rag_sources: null,
                },
            ],
            total: list.total + 1,
        },
    );
}

function requestStop(deps: ChatStreamDeps, sessionId: string, run: Run): void {
    deps.client.cancelChat(sessionId).catch((err: unknown) => {
        if (!isCurrent(sessionId, run)) return;
        // The turn ended before the stop reached it; its closing `done` is on the way.
        if (err instanceof GaiaApiError && err.status === 404) return;
        run.stopRequested = false;
        update(sessionId, run, (turn) => ({ ...turn, status: 'streaming', error: asGaiaError(err) }));
    });
}

/** The live view dropped mid-turn: pick the turn up again after the web's backoff. */
function reconnect(deps: ChatStreamDeps, sessionId: string, old: Run): void {
    const attempt = old.reconnects + 1;
    const run = newRun('attach', attempt);
    runs.set(sessionId, run);
    writeTurn(sessionId, { ...readTurn(sessionId), reconnecting: attempt });
    const start = () => {
        if (isCurrent(sessionId, run)) void follow(deps, sessionId, run, deps.client.attachToRun(sessionId, { signal: run.view.signal }));
    };
    const delay = RECONNECT_DELAYS_MS[attempt - 1] ?? 0;
    if (delay > 0) run.retryTimer = setTimeout(start, delay);
    else start();
}

async function follow(deps: ChatStreamDeps, sessionId: string, run: Run, stream: AsyncIterable<StreamEvent>) {
    try {
        for await (const event of stream) {
            if (!isCurrent(sessionId, run)) return;
            if (!run.sawEvent) {
                run.sawEvent = true;
                stopAwaitingReply(sessionId);
                update(sessionId, run, openTurn);
                if (run.stopRequested) requestStop(deps, sessionId, run);
            }
            if (event.type === 'done' || event.type === 'error') expirePendingPrompts(sessionId);
            if (event.type === 'done' && typeof event.message_id === 'number') run.replyId = event.message_id;
            update(sessionId, run, (turn) => applyEvent(turn, event));
        }
    } catch (err) {
        if (!isCurrent(sessionId, run)) return;
        // A re-attach that can't reach GAIA is another lost view, not yet a failed turn.
        const lostView =
            err instanceof GaiaStreamDroppedError || err instanceof GaiaStreamStalledError || (run.reconnects > 0 && err instanceof GaiaNetworkError);
        if (lostView && !run.stopRequested && run.reconnects < MAX_STREAM_RECONNECTS) {
            reconnect(deps, sessionId, run);
            return;
        }
        const error =
            lostView && run.reconnects >= MAX_STREAM_RECONNECTS
                ? err instanceof GaiaNetworkError
                    ? new GaiaUnreachableError(err)
                    : new GaiaStreamError('chatError.liveViewLost', { n: MAX_STREAM_RECONNECTS + 1 }, { cause: err })
                : asGaiaError(err);
        expirePendingPrompts(sessionId);
        update(sessionId, run, (turn) => ({
            ...turn,
            status: 'idle',
            pendingPermission: null,
            pendingQuestion: null,
            ...(turn.status === 'stopping' ? { stopState: 'stopped' as const, stopError: error } : { error }),
        }));
        finishRun(sessionId);
        // Drops the sent message if GAIA never took it, or shows what it saved.
        void refreshChat(deps.queryClient, sessionId);
        return;
    }
    if (!isCurrent(sessionId, run)) return;
    // A re-attach that finds no turn means it ended while the phone was away; the transcript has its answer.
    if (!run.sawEvent && run.reconnects === 0) {
        finishRun(sessionId);
        // No turn is running, but one that ended just now may be missing from a transcript read before it.
        void deps.queryClient.invalidateQueries({ queryKey: queryKeys.messages(sessionId) }, { cancelRefetch: false });
        return;
    }
    expirePendingPrompts(sessionId);
    run.ending = true;
    const holds = (list: MessageList | undefined) => holdsReply(list, run.replyId);
    await endWithTranscript(deps.queryClient, sessionId, holds, (hasReply) => {
        if (!isCurrent(sessionId, run)) return;
        finishRun(sessionId);
        const { error, stopState, stopError, liveText, events, failed, failure, policyAlert, pendingConfirmation } = readTurn(sessionId);
        // The stop note, a failure and a policy block outlive the turn; a stop GAIA never confirmed has no saved answer to show, so its text stays too.
        const kept = error || stopState || failed || policyAlert || pendingConfirmation;
        if (!kept && !hasReply && liveText) {
            writeTurn(sessionId, { ...IDLE_TURN, liveText, events });
            awaitReply(deps.queryClient, sessionId, holds);
            return;
        }
        writeTurn(
            sessionId,
            kept
                ? {
                      ...IDLE_TURN,
                      error,
                      stopState,
                      stopError,
                      failed,
                      failure,
                      policyAlert,
                      pendingConfirmation,
                      liveText: stopState === 'unconfirmed' ? liveText : '',
                  }
                : null,
        );
    });
}

/** Starts a turn; false when the text is empty or a turn is already running in this chat. */
export function sendTurn(deps: ChatStreamDeps, sessionId: string, text: string): boolean {
    const message = text.trim();
    if (!message) return false;
    const existing = runs.get(sessionId);
    // A reattach that hasn't found a running turn gives way to a new one.
    if (existing && (existing.kind !== 'attach' || existing.sawEvent)) return false;
    existing?.view.abort();
    const run = newRun('send');
    runs.set(sessionId, run);
    stopAwaitingReply(sessionId);
    writeTurn(sessionId, beginTurn());
    void (async () => {
        await showSentMessage(deps.queryClient, sessionId, message);
        if (!isCurrent(sessionId, run)) return;
        await follow(deps, sessionId, run, deps.client.sendMessage(sessionId, message, { signal: run.view.signal }));
    })();
    return true;
}

/** Asks GAIA to stop the chat's turn. The stream stays open until GAIA closes the turn. */
export function stopTurn(deps: ChatStreamDeps, sessionId: string): void {
    const run = runs.get(sessionId);
    const { status } = readTurn(sessionId);
    if (!run || run.stopRequested || (status !== 'connecting' && status !== 'streaming')) return;
    run.stopRequested = true;
    update(sessionId, run, (turn) => ({ ...turn, status: 'stopping' }));
    // Before its first event the turn may not exist on the PC yet; the stop goes out with that event.
    if (run.sawEvent) requestStop(deps, sessionId, run);
    run.graceTimer = setTimeout(() => closeUnconfirmed(sessionId, run), STOP_GRACE_MS);
}

/** GAIA never closed the stopped turn: end it here and say so, keeping what was seen. */
function closeUnconfirmed(sessionId: string, run: Run): void {
    if (!isCurrent(sessionId, run)) return;
    expirePendingPrompts(sessionId);
    run.view.abort();
    finishRun(sessionId);
    writeTurn(sessionId, {
        ...readTurn(sessionId),
        status: 'idle',
        pendingPermission: null,
        pendingQuestion: null,
        stopState: 'unconfirmed',
    });
}

/** The message that started the chat's latest exchange, as GAIA saved it. */
function lastQuestion(messages: Message[]): Message | undefined {
    return [...messages].reverse().find((m) => m.role === 'user');
}

/**
 * Asks the chat's latest question again: deletes it and everything after it, then
 * sends it afresh. Resolves false when nothing was sent (a turn is running, or the
 * question already has an answer GAIA saved that this screen had not shown).
 * `replyId` is the saved reply being retried; null when the failed reply was never saved.
 */
export async function retryLastTurn(deps: ChatStreamDeps, sessionId: string, replyId: number | null): Promise<boolean> {
    if (runs.has(sessionId)) return false;
    const { messages } = await deps.client.getMessages(sessionId);
    const question = lastQuestion(messages);
    if (!question) return false;
    const answered = messages.some((m) => m.role === 'assistant' && m.id > question.id);
    if (answered && replyId === null) {
        await refreshChat(deps.queryClient, sessionId);
        return false;
    }
    await deps.client.deleteMessagesFrom(sessionId, question.id);
    await refreshChat(deps.queryClient, sessionId);
    return sendTurn(deps, sessionId, question.content);
}

/** Follows the chat's running turn, if it has one; the turn shows once its replay arrives. */
export function attachTurn(deps: ChatStreamDeps, sessionId: string): void {
    if (runs.has(sessionId)) return;
    const run = newRun('attach');
    runs.set(sessionId, run);
    void follow(deps, sessionId, run, deps.client.attachToRun(sessionId, { signal: run.view.signal }));
}

/** Stops following the chat's turn on this phone; the turn goes on on the PC. */
export function detachTurn(sessionId: string): void {
    const run = runs.get(sessionId);
    // Its stream already closed; the turn ends on its own once the transcript is read.
    if (run?.ending) return;
    run?.view.abort();
    finishRun(sessionId);
    // An ended turn keeps its answer until the transcript holds the saved copy.
    if (awaitingReply.has(sessionId)) return;
    const { pendingConfirmation } = readTurn(sessionId);
    writeTurn(sessionId, pendingConfirmation ? { ...IDLE_TURN, pendingConfirmation } : null);
}

function subscribe(deps: ChatStreamDeps, sessionId: string): () => void {
    const first = !subscribers.has(sessionId);
    subscribers.set(sessionId, (subscribers.get(sessionId) ?? 0) + 1);
    attachTurn(deps, sessionId);
    // A phone that was backgrounded lost its connection while the turn went on, so pick it up again.
    if (first) {
        let wasBackgrounded = false;
        foregroundListeners.set(
            sessionId,
            AppState.addEventListener('change', (state) => {
                if (state === 'background') wasBackgrounded = true;
                if (state !== 'active' || !wasBackgrounded) return;
                wasBackgrounded = false;
                detachTurn(sessionId);
                attachTurn(deps, sessionId);
            }),
        );
    }
    return () => {
        const left = (subscribers.get(sessionId) ?? 1) - 1;
        if (left > 0) {
            subscribers.set(sessionId, left);
            return;
        }
        subscribers.delete(sessionId);
        foregroundListeners.get(sessionId)?.remove();
        foregroundListeners.delete(sessionId);
        detachTurn(sessionId);
    };
}

export interface ChatStream {
    /** Sends a message; false when the text is empty or a turn is already running here. */
    send(text: string): boolean;
    /** Asks GAIA to stop the running turn. */
    stop(): void;
    /** Asks the latest question again; see `retryLastTurn`. */
    retry(replyId: number | null): Promise<boolean>;
    status: ChatStreamStatus;
    /** The answer so far. A finished turn's stays until the transcript holds it; a failed one's stays beside `error`. */
    liveText: string;
    /** The agent's activity in order (status, thinking, tools, plans...). */
    events: readonly StreamEvent[];
    /** Answer it with useConfirmTool. */
    pendingPermission: PermissionRequest | null;
    /** Answer it with useAnswerQuestion. */
    pendingQuestion: QuestionRequest | null;
    /** Informational: the run ended on a tool it won't run unattended; nothing to answer. */
    pendingConfirmation: ConfirmationRequest | null;
    /** Why the last send, stop or stream failed; cleared by the next send. */
    error: GaiaError | null;
    /** GAIA closed the last turn as failed, or its answer echoes a tool's error. */
    failed: boolean | undefined;
    /** The cause a failed turn named, for its fix. */
    failure: string | undefined;
    /** The last tool call policy blocked in the turn. */
    policyAlert: ChatTurn['policyAlert'];
    /** How the last turn ended when the person stopped it; cleared by the next send. */
    stopState: ChatTurn['stopState'];
    /** What GAIA reported while stopping. */
    stopError: GaiaError | undefined;
    /** The live view dropped and this re-attach attempt (of `MAX_STREAM_RECONNECTS`) is under way; 0 when whole. */
    reconnecting: number;
}

/**
 * The chat's live turn. Every screen showing the chat shares one connection;
 * opening a chat whose turn is still running picks it up from its start.
 */
export function useChatStream(sessionId: string): ChatStream {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const turn = useChatStreamStore((state) => state.turns[sessionId] ?? IDLE_TURN);

    useEffect(() => {
        if (!client || !sessionId) return undefined;
        return subscribe({ client, queryClient }, sessionId);
    }, [client, queryClient, sessionId]);

    const send = useCallback(
        (text: string) => {
            if (client) return sendTurn({ client, queryClient }, sessionId, text);
            writeTurn(sessionId, { ...readTurn(sessionId), error: new GaiaConfigError('mobileApp.error.noConnection') });
            return false;
        },
        [client, queryClient, sessionId],
    );

    const stop = useCallback(() => {
        if (client) stopTurn({ client, queryClient }, sessionId);
    }, [client, queryClient, sessionId]);

    const retry = useCallback(
        (replyId: number | null) => {
            if (!client) return Promise.reject(new GaiaConfigError('mobileApp.error.noConnection'));
            return retryLastTurn({ client, queryClient }, sessionId, replyId);
        },
        [client, queryClient, sessionId],
    );

    return useMemo(
        () => ({
            send,
            stop,
            retry,
            status: turn.status,
            liveText: turn.liveText,
            events: turn.events,
            pendingPermission: turn.pendingPermission,
            pendingQuestion: turn.pendingQuestion,
            pendingConfirmation: turn.pendingConfirmation,
            error: turn.error,
            stopState: turn.stopState,
            stopError: turn.stopError,
            reconnecting: turn.reconnecting ?? 0,
            failed: turn.failed,
            failure: turn.failure,
            policyAlert: turn.policyAlert,
        }),
        [send, stop, retry, turn],
    );
}
