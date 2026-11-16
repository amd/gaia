// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

import { translator } from '@webui/i18n/translator';
import type { StreamEvent } from '@webui/types';
import { dropErrorsEchoedInAnswer } from '@webui/utils/answerErrors.core';
import { agentEventToStep } from '@webui/utils/streamSteps.core';

import { AGENT_EVENT_TYPES, RESOLVES_PROMPTS } from '@/api/constants';
import { GaiaTurnError, type GaiaError } from '@/api/errors';

/**
 * - `idle`: no turn is running in this chat on this phone.
 * - `connecting`: a message was sent and GAIA has not started answering it.
 * - `streaming`: the turn's events are arriving; a reattached turn replays from its start.
 * - `stopping`: Stop was pressed; the turn ends when GAIA confirms it.
 */
export type ChatStreamStatus = 'idle' | 'connecting' | 'streaming' | 'stopping';

/** A tool call the turn is blocked on until it is allowed or denied. */
export type PermissionRequest = StreamEvent & { type: 'permission_request' };

/** A question the turn is blocked on until it is answered. */
export type QuestionRequest = StreamEvent & { type: 'needs_input'; request_id: string };

/** The run ended on a tool GAIA won't run unattended; informational, it has no id and nothing to answer. */
export type ConfirmationRequest = StreamEvent & { type: 'needs_confirmation' };

export interface ChatTurn {
    status: ChatStreamStatus;
    /** The answer so far. */
    liveText: string;
    /** The agent's activity in order; back-to-back `thinking` pieces are joined into one event. */
    events: readonly StreamEvent[];
    pendingPermission: PermissionRequest | null;
    pendingQuestion: QuestionRequest | null;
    // Outlives the turn on purpose: the card is informational and often arrives after the run.
    pendingConfirmation: ConfirmationRequest | null;
    error: GaiaError | null;
    /** A tool ran since the last answer text, so the next text starts a new paragraph. */
    toolSinceText: boolean;
    /** How a turn the person stopped ended; `unconfirmed` means GAIA never confirmed the stop. */
    stopState?: 'stopped' | 'unconfirmed';
    /** What GAIA reported while stopping. */
    stopError?: GaiaError;
    /** Re-attach attempt under way after the live view dropped; cleared when the replay starts. */
    reconnecting?: number;
    /** GAIA closed the turn as failed, or its answer only echoes a tool's error. */
    failed?: boolean;
    /** The cause a failed turn named (`done.failure`), for its fix. */
    failure?: string;
    /** The last tool call governance policy blocked; `seq` tells one alert from the next. */
    policyAlert?: { tool: string; receiptId?: string; seq: number };
}

export const IDLE_TURN: ChatTurn = {
    status: 'idle',
    liveText: '',
    events: [],
    pendingPermission: null,
    pendingQuestion: null,
    pendingConfirmation: null,
    error: null,
    toolSinceText: false,
};

/** A message was just sent: nothing of the previous turn is kept. */
export function beginTurn(): ChatTurn {
    return { ...IDLE_TURN, status: 'connecting' };
}

/** The turn's first event arrived. A reattached turn replays from its start, so this starts clean. */
export function openTurn(turn: ChatTurn): ChatTurn {
    return { ...IDLE_TURN, status: turn.status === 'stopping' ? 'stopping' : 'streaming' };
}

function withoutPrompts(turn: ChatTurn): ChatTurn {
    if (!turn.pendingPermission && !turn.pendingQuestion) return turn;
    return { ...turn, pendingPermission: null, pendingQuestion: null };
}

function appendEvent(events: readonly StreamEvent[], event: StreamEvent): readonly StreamEvent[] {
    const last = events[events.length - 1];
    if (event.type === 'thinking' && last?.type === 'thinking') {
        return [...events.slice(0, -1), { ...last, content: (last.content ?? '') + (event.content ?? '') }];
    }
    return [...events, event];
}

function applyAgentEvent(turn: ChatTurn, event: StreamEvent): ChatTurn {
    let next = RESOLVES_PROMPTS.has(event.type) ? withoutPrompts(turn) : turn;
    if (event.type === 'permission_request') {
        next = { ...next, pendingPermission: event as PermissionRequest };
    }
    // A question without an id can't be answered; it stays visible in `events`.
    if (event.type === 'needs_input' && event.request_id) {
        next = { ...next, pendingQuestion: event as QuestionRequest };
    }
    if (event.type === 'needs_confirmation') {
        next = { ...next, pendingConfirmation: event as ConfirmationRequest };
    }
    if (event.type === 'tool_start') {
        next = { ...next, toolSinceText: true };
    }
    if (event.type === 'policy_alert') {
        next = { ...next, policyAlert: { tool: event.tool ?? '', receiptId: event.receipt_id, seq: turn.events.length } };
    }
    // The text so far narrated a tool call, was reasoning, or was cut off: it is not the answer.
    if (event.type === 'commentary' || event.retracts_answer) {
        next = { ...next, liveText: '', toolSinceText: false };
    }
    return { ...next, events: appendEvent(next.events, event) };
}

/** The answer is, word for word, an error step of the turn: the reply stands in for a failure. */
function echoesError(events: readonly StreamEvent[], answer: string): boolean {
    const t = translator('en');
    const steps = events.flatMap((event) => agentEventToStep(event, { current: 0 }, t) ?? []);
    return dropErrorsEchoedInAnswer(steps, answer).echoed;
}

/** One stream event folded into the turn, as the web Agent UI's chat view folds it. */
export function applyEvent(turn: ChatTurn, event: StreamEvent): ChatTurn {
    switch (event.type) {
        case 'chunk': {
            if (!event.content) return turn;
            const separator = turn.toolSinceText && turn.liveText ? '\n\n' : '';
            return { ...withoutPrompts(turn), liveText: turn.liveText + separator + event.content, toolSinceText: false };
        }
        case 'answer':
            // The whole answer, not a piece of it.
            if (!event.content) return turn;
            return { ...withoutPrompts(turn), liveText: event.content, toolSinceText: false };
        case 'done': {
            // A `done` with no message id was made up by the client when the stream closed unreported.
            const stopState = event.cancelled === true ? 'stopped' : turn.status === 'stopping' && event.message_id == null ? 'unconfirmed' : undefined;
            const answer = event.content || turn.liveText;
            const failed = event.failed === true || echoesError(turn.events, answer);
            return {
                ...withoutPrompts(turn),
                liveText: answer,
                ...(stopState ? { stopState } : null),
                ...(failed ? { failed, failure: event.failed ? event.failure : undefined } : null),
            };
        }
        case 'error': {
            const error = new GaiaTurnError(event.content ?? '');
            // An error while stopping is part of the stop, not a failed answer.
            if (turn.status === 'stopping') return { ...withoutPrompts(turn), stopState: 'stopped', stopError: error };
            return { ...withoutPrompts(turn), error };
        }
        default:
            return AGENT_EVENT_TYPES.has(event.type) ? applyAgentEvent(turn, event) : turn;
    }
}

/** The permission prompt `confirmId` was answered; with no id, whichever one is open. */
export function permissionAnswered(turn: ChatTurn, confirmId?: string | null): ChatTurn {
    const pending = turn.pendingPermission;
    if (!pending || (confirmId && pending.confirm_id !== confirmId)) return turn;
    return { ...turn, pendingPermission: null };
}

/** The card's local Dismiss: nothing is sent, the card just stops showing. */
export function confirmationDismissed(turn: ChatTurn): ChatTurn {
    return turn.pendingConfirmation ? { ...turn, pendingConfirmation: null } : turn;
}

export function questionAnswered(turn: ChatTurn, requestId: string): ChatTurn {
    return turn.pendingQuestion?.request_id === requestId ? { ...turn, pendingQuestion: null } : turn;
}

export interface ResolvedPrompt {
    state: 'answered' | 'expired';
    /** What to show as "You answered": a question's chosen label or typed text. Never kept for a sensitive question. */
    answer?: string;
}

interface ChatStreamsState {
    /** By chat id; a chat with no entry is idle. */
    turns: Readonly<Record<string, ChatTurn>>;
    /** How each prompt ended, by chat id then prompt id (a question's `request_id`, an approval's `confirm_id`). Outlives turns. */
    resolved: Readonly<Record<string, Readonly<Record<string, ResolvedPrompt>>>>;
}

// Live turns only: once a turn ends, its messages come from the transcript query.
export const useChatStreamStore = create<ChatStreamsState>(() => ({ turns: {}, resolved: {} }));

/** An answer is final; an expiry never replaces one. */
export function markPromptResolved(sessionId: string, promptId: string, resolution: ResolvedPrompt): void {
    useChatStreamStore.setState((state) => {
        const chat = state.resolved[sessionId] ?? {};
        if (chat[promptId]?.state === 'answered' && resolution.state === 'expired') return state;
        return { resolved: { ...state.resolved, [sessionId]: { ...chat, [promptId]: resolution } } };
    });
}

/** The turn is over with a prompt still open: it can no longer be answered. */
export function expirePendingPrompts(sessionId: string): void {
    const turn = useChatStreamStore.getState().turns[sessionId];
    if (turn?.pendingPermission?.confirm_id) markPromptResolved(sessionId, turn.pendingPermission.confirm_id, { state: 'expired' });
    if (turn?.pendingQuestion) markPromptResolved(sessionId, turn.pendingQuestion.request_id, { state: 'expired' });
}

export function useResolvedPrompt(sessionId: string, promptId: string): ResolvedPrompt | undefined {
    return useChatStreamStore((state) => state.resolved[sessionId]?.[promptId]);
}

export function readTurn(sessionId: string): ChatTurn {
    return useChatStreamStore.getState().turns[sessionId] ?? IDLE_TURN;
}

export function writeTurn(sessionId: string, turn: ChatTurn | null): void {
    useChatStreamStore.setState((state) => {
        const turns = { ...state.turns };
        if (turn === null) delete turns[sessionId];
        else turns[sessionId] = turn;
        return { turns };
    });
}

/** Changes a chat's live turn; a chat with none is left alone. */
export function updateTurn(sessionId: string, change: (turn: ChatTurn) => ChatTurn): void {
    const turn = useChatStreamStore.getState().turns[sessionId];
    if (!turn) return;
    const next = change(turn);
    if (next !== turn) writeTurn(sessionId, next);
}
