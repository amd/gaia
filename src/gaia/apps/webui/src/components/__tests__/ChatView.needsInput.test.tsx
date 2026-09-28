// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Contract tests for the `needs_input` wiring in ChatView (#2595), pattern-
 * matched off ChatView.confirmation.test.tsx:
 *
 *   - A `needs_input` agent event rides the existing generic tool-card
 *     mechanism (`cards` store), carrying session_id/request_id so the
 *     rendered NeedsInputCard can answer without extra context plumbing.
 *   - It is a separate, non-blocking-to-the-UI surface: dispatching it must
 *     never touch notificationStore's permission-request path.
 *   - A malformed event (missing request_id) is dropped loudly (logged), not
 *     silently turned into an unanswerable card.
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
    title: 'Existing chat',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'email',
};

const EMAIL_AGENT: AgentInfo = {
    id: 'email',
    name: 'Email Agent',
    description: 'Triage and reply to email.',
    source: 'builtin',
    conversation_starters: ['Triage my inbox'],
    models: [],
    tags: ['email'],
    icon: 'mail',
    tools_count: 3,
};

const needsInputEvent = {
    type: 'needs_input',
    request_id: 'req-1',
    question: 'Which mailbox should I use?',
    options: [{ value: 'gmail', label: 'Gmail', description: '' }],
    allow_free_text: true,
    sensitive: false,
} as unknown as StreamEvent;

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
    mockedApi.respondToInput.mockResolvedValue({ status: 'ok', request_id: 'req-1' });
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        capturedCallbacks = cbs as api.StreamCallbacks;
        return new AbortController();
    });

    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true,
        value: vi.fn(),
    });

    useChatStore.setState({
        agents: [EMAIL_AGENT],
        activeAgentId: 'email',
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

async function driveSend() {
    render(<ChatView sessionId={SESSION.id} />);

    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message input'), {
            target: { value: 'triage my inbox' },
        });
        fireEvent.click(screen.getByLabelText('Send message'));
    });

    expect(capturedCallbacks).not.toBeNull();
}

describe('ChatView needs_input wiring (#2595)', () => {
    it('appends a needs_input card carrying session_id + the event payload', async () => {
        await driveSend();

        act(() => {
            capturedCallbacks!.onAgentEvent(needsInputEvent);
        });

        const cards = useChatStore.getState().cards;
        expect(cards).toHaveLength(1);
        expect(cards[0].render).toBe('needs_input');
        expect(cards[0].data).toEqual({
            session_id: SESSION.id,
            request_id: 'req-1',
            question: 'Which mailbox should I use?',
            options: [{ value: 'gmail', label: 'Gmail', description: '' }],
            allow_free_text: true,
            sensitive: false,
        });

        expect(screen.getByText('Which mailbox should I use?')).toBeInTheDocument();
    });

    it('drops a needs_input event missing request_id without creating a card', async () => {
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        await driveSend();

        act(() => {
            capturedCallbacks!.onAgentEvent({
                ...needsInputEvent,
                request_id: undefined,
            } as unknown as StreamEvent);
        });

        expect(useChatStore.getState().cards).toHaveLength(0);
        expect(consoleError).toHaveBeenCalled();
        consoleError.mockRestore();
    });

    it('does not touch notificationStore permission-request path', async () => {
        await driveSend();

        expect(useNotificationStore.getState().notifications).toEqual([]);
        expect(selectActivePermissionPrompt(useNotificationStore.getState())).toBeNull();

        act(() => {
            capturedCallbacks!.onAgentEvent(needsInputEvent);
        });

        expect(useNotificationStore.getState().notifications).toEqual([]);
        expect(selectActivePermissionPrompt(useNotificationStore.getState())).toBeNull();
    });

    it('answering the rendered card calls respondToInput with the session and request id', async () => {
        await driveSend();

        act(() => {
            capturedCallbacks!.onAgentEvent(needsInputEvent);
        });

        fireEvent.click(screen.getByRole('button', { name: 'Gmail' }));

        await act(async () => {});

        expect(mockedApi.respondToInput).toHaveBeenCalledWith(SESSION.id, 'req-1', 'gmail');
    });
});
