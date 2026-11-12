// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A new chat's first message shows the moment the chat opens, with GAIA's
 * live line under it: no skeleton bars and no "New Task" title while the
 * empty history loads (#7349).
 */

import { act, render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Session, StreamEvent, SystemStatus } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1', title: 'New Task', created_at: '2026-06-10T00:00:00Z', updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen', system_prompt: null, message_count: 0, document_ids: [], agent_type: 'gaia',
};
const READY = { lemonade_running: true, initialized: true, init_state: 'ready', init_tasks: [] } as unknown as SystemStatus;
const QUESTION = 'Give me three tips for better sleep.';
const ANSWER = 'Keep a fixed wake time.';

let finishLoad: () => void = () => {};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getMessages.mockImplementation(() => new Promise((resolve) => {
        finishLoad = () => resolve({ messages: [], total: 0 });
    }));
    mockedApi.getMessageCount.mockResolvedValue(0);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.getSystemStatus.mockResolvedValue(READY);
    mockedApi.sendMessageStream.mockImplementation(() => new AbortController());
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        sessions: [SESSION], currentSessionId: SESSION.id, messages: [], documents: [],
        isStreaming: false, streamingContent: '', agentSteps: [], cards: [], isLoadingMessages: false,
        pendingPrompt: { sessionId: SESSION.id, text: QUESTION }, systemStatus: READY, messageQueues: {}, pendingDeleteIds: [],
    });
});

const bubbles = () => Array.from(document.querySelectorAll('.msg-user')).map((el) => el.textContent);

describe('the first message of a new chat', () => {
    it('shows at once with the live line, not skeleton bars', () => {
        render(<ChatView sessionId={SESSION.id} />);

        expect(document.querySelector('.skeleton-messages')).toBeNull();
        expect(bubbles()).toEqual([QUESTION]);
        expect(document.querySelector('.streaming-bubble .thinking-indicator')).not.toBeNull();
        expect(document.querySelector('.task-title')?.textContent).toBe('New chat');
    });

    it('keeps one bubble, the same element, once it is sent', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        const shown = document.querySelector('.msg-user');

        await act(async () => { finishLoad(); });
        await waitFor(() => expect(mockedApi.sendMessageStream).toHaveBeenCalledWith(SESSION.id, QUESTION, expect.anything()));

        expect(bubbles()).toEqual([QUESTION]);
        expect(document.querySelector('.msg-user')).toBe(shown);
    });

    it('keeps its bubble through the saved ids, and its answer settles in place', async () => {
        let callbacks: api.StreamCallbacks | null = null;
        mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
            callbacks = cbs as api.StreamCallbacks;
            return new AbortController();
        });
        mockedApi.getSession.mockResolvedValue({ ...SESSION, title: 'Sleep tips', message_count: 2 });
        render(<ChatView sessionId={SESSION.id} />);
        const shown = document.querySelector('.msg-user');

        await act(async () => { finishLoad(); });
        await waitFor(() => expect(callbacks).not.toBeNull());
        mockedApi.getMessages.mockResolvedValue({
            messages: [
                { id: 41, session_id: SESSION.id, role: 'user', content: QUESTION, created_at: '', rag_sources: null },
                { id: 42, session_id: SESSION.id, role: 'assistant', content: ANSWER, created_at: '', rag_sources: null },
            ],
            total: 2,
        });
        await act(async () => {
            callbacks!.onDone({ type: 'done', message_id: 42, content: ANSWER } as StreamEvent);
        });

        await waitFor(() => expect(document.querySelector('[data-message-id="42"]')).not.toBeNull());
        await waitFor(() => expect(document.querySelector('[data-message-id="41"]')).not.toBeNull());
        expect(bubbles()).toEqual([QUESTION]);
        expect(document.querySelector('.msg-user')).toBe(shown);
        expect(document.querySelector('[data-message-id="42"]')?.classList.contains('msg-settled')).toBe(true);
    });

    it('offers no message actions until the first message is saved', async () => {
        let callbacks: api.StreamCallbacks | null = null;
        mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
            callbacks = cbs as api.StreamCallbacks;
            return new AbortController();
        });
        mockedApi.getSession.mockResolvedValue({ ...SESSION, title: 'Sleep tips', message_count: 2 });
        render(<ChatView sessionId={SESSION.id} />);

        const actionsOn = (sel: string) => Array.from(document.querySelectorAll(`${sel} .msg-actions button`))
            .map((b) => b.getAttribute('aria-label'));
        const NO_ACTIONS = ['Edit message', 'Resend message', 'Delete message', 'Try again'];
        expect(document.querySelector('[data-message-id="-2"]')).not.toBeNull();
        expect(actionsOn('[data-message-id="-2"]')).not.toEqual(expect.arrayContaining([expect.toBeOneOf(NO_ACTIONS)]));
        expect(document.querySelector('.msg-try-again')).toBeNull();

        await act(async () => { finishLoad(); });
        await waitFor(() => expect(callbacks).not.toBeNull());
        expect(actionsOn('.msg-user')).not.toEqual(expect.arrayContaining([expect.toBeOneOf(NO_ACTIONS)]));

        mockedApi.getMessages.mockResolvedValue({
            messages: [
                { id: 41, session_id: SESSION.id, role: 'user', content: QUESTION, created_at: '', rag_sources: null },
                { id: 42, session_id: SESSION.id, role: 'assistant', content: ANSWER, created_at: '', rag_sources: null },
            ],
            total: 2,
        });
        await act(async () => {
            callbacks!.onDone({ type: 'done', message_id: 42, content: ANSWER } as StreamEvent);
        });

        await waitFor(() => expect(actionsOn('[data-message-id="41"]')).toEqual(
            expect.arrayContaining(['Edit message', 'Resend message', 'Delete message']),
        ));
        expect(document.querySelector('[data-message-id="-2"]')).toBeNull();
        await waitFor(() => expect(actionsOn('[data-message-id="42"]')).toContain('Try again'));
    });
});
