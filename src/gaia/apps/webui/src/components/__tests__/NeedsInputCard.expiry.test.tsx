// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A question card is answerable only while the agent still waits on it. Once
 * its turn ends or its wait runs out it must say so and drop its buttons, and
 * a click racing that must get a plain message, not "No active chat session".
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { NeedsInputCard, QUESTION_EXPIRED_NOTE } from '../render/NeedsInputCard';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
import type { Session, StreamEvent } from '../../types';
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
    agent_type: 'gaia',
};

const card = {
    session_id: SESSION.id,
    request_id: 'req-1',
    question: 'I stopped instead of running it. Allow it once?',
    options: [
        { value: 'Allow once', label: 'Allow once', description: '' },
        { value: "Don't run it", label: "Don't run it", description: '' },
    ],
    allow_free_text: true,
    sensitive: false,
};

function gone(status: number): Error {
    return Object.assign(new Error('No active chat session found for this session ID'), { httpStatus: status });
}

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ liveQuestions: {} });
    mockedApi.respondToInput.mockResolvedValue({ status: 'ok', request_id: 'req-1' });
});

afterEach(() => {
    vi.useRealTimers();
});

describe('NeedsInputCard expiry', () => {
    it('a card whose question is not live has no buttons and says it expired', () => {
        render(<NeedsInputCard data={card} />);
        expect(screen.queryByRole('button', { name: 'Allow once' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument();
        expect(screen.queryByPlaceholderText(/type your answer/i)).not.toBeInTheDocument();
        expect(screen.getByText(QUESTION_EXPIRED_NOTE)).toBeInTheDocument();
        expect(screen.getByText(card.question)).toBeInTheDocument();
    });

    it('a live card closes when its turn ends', () => {
        useChatStore.getState().openQuestion('req-1', SESSION.id, null);
        render(<NeedsInputCard data={card} />);
        expect(screen.getByRole('button', { name: 'Allow once' })).toBeEnabled();

        act(() => useChatStore.getState().closeSessionQuestions(SESSION.id));

        expect(screen.queryByRole('button', { name: 'Allow once' })).not.toBeInTheDocument();
        expect(screen.getByText(QUESTION_EXPIRED_NOTE)).toBeInTheDocument();
    });

    it('closes at the backend deadline, not a second before', () => {
        vi.useFakeTimers();
        useChatStore.getState().openQuestion('req-1', SESSION.id, 300);
        render(<NeedsInputCard data={card} />);

        act(() => { vi.advanceTimersByTime(299_000); });
        expect(screen.getByRole('button', { name: 'Allow once' })).toBeEnabled();

        act(() => { vi.advanceTimersByTime(1_000); });
        expect(screen.queryByRole('button', { name: 'Allow once' })).not.toBeInTheDocument();
        expect(screen.getByText(QUESTION_EXPIRED_NOTE)).toBeInTheDocument();
        expect(useChatStore.getState().liveQuestions['req-1']).toBeUndefined();
    });

    it('closing another session leaves this question live', () => {
        useChatStore.getState().openQuestion('req-1', SESSION.id, null);
        render(<NeedsInputCard data={card} />);
        act(() => useChatStore.getState().closeSessionQuestions('other-session'));
        expect(screen.getByRole('button', { name: 'Allow once' })).toBeEnabled();
    });

    it('a late click answered 404 shows the expired note, not the raw error', async () => {
        useChatStore.getState().openQuestion('req-1', SESSION.id, null);
        mockedApi.respondToInput.mockRejectedValue(gone(404));
        render(<NeedsInputCard data={card} />);

        fireEvent.click(screen.getByRole('button', { name: 'Allow once' }));
        await act(async () => {});

        expect(screen.queryByText(/No active chat session/)).not.toBeInTheDocument();
        expect(screen.getByText(QUESTION_EXPIRED_NOTE)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Allow once' })).not.toBeInTheDocument();
        expect(useChatStore.getState().liveQuestions['req-1']).toBeUndefined();
    });

    it.each([
        [409, 'Could not deliver the answer: sidecar busy'],
        [500, 'Server error.'],
    ])('a %i failure keeps the card answerable', async (status, message) => {
        useChatStore.getState().openQuestion('req-1', SESSION.id, null);
        mockedApi.respondToInput.mockRejectedValue(Object.assign(new Error(message), { httpStatus: status }));
        render(<NeedsInputCard data={card} />);

        fireEvent.click(screen.getByRole('button', { name: 'Allow once' }));
        await act(async () => {});

        expect(screen.getByText(message)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Allow once' })).toBeEnabled();
    });

    it('a successful answer closes the question', async () => {
        useChatStore.getState().openQuestion('req-1', SESSION.id, null);
        render(<NeedsInputCard data={card} />);
        fireEvent.click(screen.getByRole('button', { name: 'Allow once' }));
        await act(async () => {});
        expect(screen.getByText('Answered')).toBeInTheDocument();
        expect(useChatStore.getState().liveQuestions['req-1']).toBeUndefined();
    });
});

describe('ChatView question lifecycle', () => {
    let callbacks: api.StreamCallbacks | null = null;

    beforeEach(() => {
        callbacks = null;
        mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
        mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
        mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
        mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
        mockedApi.cancelStream.mockResolvedValue(undefined as never);
        mockedApi.updateSession.mockResolvedValue(undefined as never);
        mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
            callbacks = cbs as api.StreamCallbacks;
            return new AbortController();
        });
        Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
        useChatStore.setState({
            agents: [],
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
            liveQuestions: {},
        });
        useNotificationStore.setState({ notifications: [], showPanel: false, typeFilter: null });
    });

    async function send() {
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'go' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        expect(callbacks).not.toBeNull();
        act(() => {
            callbacks!.onAgentEvent({
                type: 'needs_input',
                request_id: 'req-1',
                question: card.question,
                options: card.options,
                allow_free_text: true,
                sensitive: false,
                timeout_seconds: 300,
            } as unknown as StreamEvent);
        });
    }

    it('a needs_input event opens the question with its deadline', async () => {
        const before = Date.now();
        await send();
        const q = useChatStore.getState().liveQuestions['req-1'];
        expect(q?.sessionId).toBe(SESSION.id);
        expect(q?.expiresAt).toBeGreaterThanOrEqual(before + 300_000);
    });

    it('the header says it waits on the user even after an unrelated event arrives', async () => {
        await send();
        act(() => {
            callbacks!.onAgentEvent({ type: 'status', status: 'info', message: 'Saved memory' } as unknown as StreamEvent);
        });
        expect(useChatStore.getState().awaitingUser).toBe(false);
        await waitFor(() => expect(document.querySelector('.thinking-indicator-text')?.textContent).toBe('Waiting for you'));
    });

    it('the turn ending closes its questions', async () => {
        await send();
        expect(useChatStore.getState().liveQuestions['req-1']).toBeDefined();
        act(() => {
            callbacks!.onDone({ type: 'done', content: 'Done.' } as unknown as StreamEvent);
        });
        expect(useChatStore.getState().liveQuestions['req-1']).toBeUndefined();
    });

    it('a stream error closes its questions', async () => {
        await send();
        expect(useChatStore.getState().liveQuestions['req-1']).toBeDefined();
        act(() => {
            callbacks!.onError(new Error('boom'));
        });
        expect(useChatStore.getState().liveQuestions['req-1']).toBeUndefined();
    });
});
