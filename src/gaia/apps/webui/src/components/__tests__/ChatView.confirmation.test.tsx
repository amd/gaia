// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Contract tests for the `needs_confirmation` wiring in ChatView (issue
 * #2109, Increment 2, stateless D1):
 *
 *   - A `needs_confirmation` agent event rides the existing generic
 *     tool-card mechanism (#2108's `cards` store) — no bespoke branch that
 *     duplicates card storage. Pattern-matched off
 *     `ChatView.cards.test.tsx`.
 *   - It appears at stream position (in the live streaming bubble) and
 *     coexists with the terminal `final` answer that follows.
 *   - It is a separate, non-blocking surface: dispatching it must NEVER
 *     touch `notificationStore`'s permission-request path or any
 *     `confirm_id`-keyed state (unlike `tool_confirm` / `permission_request`
 *     handled a few lines above in ChatView's `onAgentEvent`).
 *
 * `needs_confirmation` is not yet in `StreamEventType` (types/index.ts) nor
 * in `AGENT_EVENT_TYPES` (services/api.ts), and ChatView's `onAgentEvent`
 * has no branch for it yet — so these tests are expected to fail (red)
 * until Increment 2 lands. `capturedCallbacks!.onAgentEvent(...)` is called
 * directly (bypassing SSE parsing), matching the established convention in
 * ChatView.cards.test.tsx; the SSE-dispatch-level contract (AGENT_EVENT_TYPES
 * routing) is pinned separately in services/__tests__/api.confirmation.test.ts.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore, selectActivePermissionPrompt } from '../../stores/notificationStore';
import type { AgentInfo, Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1',
    // Deliberately NOT 'New Task' — skips onDone's auto-title branch entirely.
    title: 'Existing chat',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'doc',
};

const DOC_AGENT: AgentInfo = {
    id: 'doc',
    name: 'Doc Agent',
    description: 'Search and answer questions from indexed documents.',
    source: 'builtin',
    conversation_starters: ['Find contract clauses'],
    models: [],
    tags: ['rag', 'files'],
    icon: 'file-text',
    tools_count: 3,
};

const needsConfirmationEvent = {
    type: 'needs_confirmation',
    action: 'send_now',
    summary: 'Send the drafted email to alice@example.com now?',
} as unknown as StreamEvent;

// The sidecar's hand-off guidance text (D1: the deterministic-call route,
// e.g. a fixed-function /confirm link, remains the actual approve path).
const FINAL_ANSWER_TEXT =
    'To send this email, open the confirmation link in your task list — I cannot send it myself.';

let capturedCallbacks: api.StreamCallbacks | null = null;

beforeEach(() => {
    vi.clearAllMocks();
    capturedCallbacks = null;

    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [],
        total: 0,
        total_size_bytes: 0,
        total_chunks: 0,
    });
    mockedApi.cancelStream.mockResolvedValue(undefined as never);
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        capturedCallbacks = cbs as api.StreamCallbacks;
        return new AbortController();
    });

    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true,
        value: vi.fn(),
    });

    useChatStore.setState({
        agents: [DOC_AGENT],
        activeAgentId: 'doc',
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

    useNotificationStore.setState({
        notifications: [],
        showPanel: false,
        typeFilter: null,
    });
});

afterEach(() => {
    vi.useRealTimers();
});

/** Type/render + click Send, and wait for sendMessageStream to be invoked. */
async function driveSend() {
    render(<ChatView sessionId={SESSION.id} />);

    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message input'), {
            target: { value: 'send the drafted email' },
        });
        fireEvent.click(screen.getByLabelText('Send message'));
    });

    expect(capturedCallbacks).not.toBeNull();
}

describe('ChatView needs_confirmation wiring (#2109, stateless D1)', () => {
    it('appends a needs_confirmation card carrying {action, summary} and shows it in the live streaming bubble', async () => {
        await driveSend();

        act(() => {
            capturedCallbacks!.onAgentEvent(needsConfirmationEvent);
        });

        const cards = useChatStore.getState().cards;
        expect(cards).toHaveLength(1);
        expect(cards[0].render).toBe('needs_confirmation');
        expect(cards[0].data).toEqual({
            action: 'send_now',
            summary: 'Send the drafted email to alice@example.com now?',
        });

        // Live bubble renders the card mid-stream, before any onDone —
        // i.e. it appears "at stream position", like tool cards (#2108).
        expect(screen.getByText('Send the drafted email to alice@example.com now?')).toBeInTheDocument();
    });

    it('does NOT add a run_id or confirm_id to the card payload (no passthrough in this increment)', async () => {
        await driveSend();

        act(() => {
            capturedCallbacks!.onAgentEvent({
                ...needsConfirmationEvent,
                confirm_id: 'should-be-ignored',
                run_id: 'should-be-ignored',
            } as unknown as StreamEvent);
        });

        const card = useChatStore.getState().cards[0];
        expect(card.data).not.toHaveProperty('run_id');
        expect(card.data).not.toHaveProperty('confirm_id');
    });

    it('coexists with the terminal final answer that follows (persists onto the finalized message)', async () => {
        vi.useFakeTimers();
        try {
            await driveSend();

            act(() => {
                capturedCallbacks!.onAgentEvent(needsConfirmationEvent);
            });

            mockedApi.getMessages.mockResolvedValue({
                messages: [
                    {
                        id: 10,
                        session_id: SESSION.id,
                        role: 'user',
                        content: 'send the drafted email',
                        created_at: '2026-07-16T00:00:00.000Z',
                        rag_sources: null,
                    },
                    {
                        id: 11,
                        session_id: SESSION.id,
                        role: 'assistant',
                        content: FINAL_ANSWER_TEXT,
                        created_at: '2026-07-16T00:00:01.000Z',
                        rag_sources: null,
                    },
                ],
                total: 2,
            });

            act(() => {
                capturedCallbacks!.onDone({ type: 'done', content: FINAL_ANSWER_TEXT } as unknown as StreamEvent);
            });

            act(() => {
                vi.advanceTimersByTime(400);
            });
            await act(async () => {});

            // Both the confirmation card AND the final hand-off guidance
            // text are visible together — the card does not replace or
            // block the final answer.
            expect(screen.getByText('Send the drafted email to alice@example.com now?')).toBeInTheDocument();
            expect(screen.getByText(FINAL_ANSWER_TEXT)).toBeInTheDocument();

            const assistantMsg = useChatStore.getState().messages.find((m) => m.role === 'assistant');
            expect(assistantMsg?.cards).toHaveLength(1);
            expect(assistantMsg?.cards?.[0].render).toBe('needs_confirmation');
        } finally {
            vi.useRealTimers();
        }
    });

    it('does not touch notificationStore permission-request path or confirm_id-keyed state (isolation from PermissionPrompt)', async () => {
        await driveSend();

        // Baseline: nothing pending before the event.
        expect(useNotificationStore.getState().notifications).toEqual([]);
        expect(selectActivePermissionPrompt(useNotificationStore.getState())).toBeNull();

        act(() => {
            capturedCallbacks!.onAgentEvent(needsConfirmationEvent);
        });

        // needs_confirmation must never create a permission_request
        // notification — PermissionPrompt (mounted at the App level, keyed
        // off selectActivePermissionPrompt) must stay entirely uninvolved.
        expect(useNotificationStore.getState().notifications).toEqual([]);
        expect(selectActivePermissionPrompt(useNotificationStore.getState())).toBeNull();
    });

    it('does not call confirmTool/confirmToolExecution for a needs_confirmation event', async () => {
        await driveSend();

        act(() => {
            capturedCallbacks!.onAgentEvent(needsConfirmationEvent);
        });

        expect(mockedApi.confirmTool).not.toHaveBeenCalled();
        expect(mockedApi.confirmToolExecution).not.toHaveBeenCalled();
    });
});
