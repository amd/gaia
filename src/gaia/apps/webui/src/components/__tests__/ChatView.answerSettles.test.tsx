// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Finishing a turn changes the footer, not the transcript (#7350): the answer
 * takes the live bubble's place without a second fade-in, and the question
 * keeps its bubble when the server's copy replaces its temporary id.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { carryMessageKeys } from '../../utils/chatStream';
import type { Message, Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1', title: 'Canals', created_at: '2026-11-04T00:00:00Z', updated_at: '2026-11-04T00:00:00Z',
    model: 'qwen', system_prompt: null, message_count: 0, document_ids: [], agent_type: 'gaia',
};
const QUESTION = 'Name a canal.';
const ANSWER = 'The **Suez Canal**.';

const row = (id: number, role: 'user' | 'assistant', content: string): Message => ({
    id, session_id: SESSION.id, role, content, created_at: '2026-11-04T00:00:00Z', rag_sources: null,
});

let callbacks: api.StreamCallbacks | null = null;

beforeEach(() => {
    vi.clearAllMocks();
    callbacks = null;
    mockedApi.getMessages
        .mockResolvedValueOnce({ messages: [], total: 0 })
        .mockResolvedValue({ messages: [row(41, 'user', QUESTION), row(42, 'assistant', ANSWER)], total: 2 });
    mockedApi.getMessageCount.mockResolvedValue(2);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        callbacks = cbs as api.StreamCallbacks;
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        sessions: [SESSION], currentSessionId: SESSION.id, messages: [], documents: [],
        isStreaming: false, streamingContent: '', agentSteps: [], cards: [], isLoadingMessages: false,
        pendingPrompt: null, systemStatus: null,
    });
});


describe('a finished answer', () => {
    it('settles in place: no second entrance, and the question keeps its bubble', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: QUESTION } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        const question = document.querySelector('.msg-user');
        expect(question?.textContent).toBe(QUESTION);

        await act(async () => {
            callbacks!.onDone({ type: 'done', message_id: 42, content: ANSWER } as StreamEvent);
        });

        await waitFor(() => expect(document.querySelector('[data-message-id="42"]')).not.toBeNull());
        expect(document.querySelector('[data-message-id="42"]')?.classList.contains('msg-settled')).toBe(true);
        // The server's copy of the question arrives under id 41; its bubble stays.
        await waitFor(() => expect(document.querySelector('[data-message-id="41"]')).not.toBeNull());
        expect(document.querySelector('.msg-user')).toBe(question);
    });

    it('is drawn once when the stream ends, never redrawn with an entrance', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: QUESTION } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        await act(async () => {
            callbacks!.onDone({ type: 'done', message_id: 42, content: ANSWER } as StreamEvent);
        });

        // Every answer on screen is the settled one: no live bubble redrawn to play its entrance again.
        const answer = document.querySelector('[data-message-id="42"] .msg-assistant');
        expect(answer).not.toBeNull();
        expect(document.querySelectorAll('.msg-assistant')).toHaveLength(1);
        expect(answer?.parentElement?.classList.contains('msg-settled')).toBe(true);

        await act(async () => { await new Promise((done) => setTimeout(done, 400)); });
        expect(document.querySelector('[data-message-id="42"] .msg-assistant')).toBe(answer);
        expect(document.querySelectorAll('.msg-assistant')).toHaveLength(1);
        // The saved copy replaces the shown one in place, too.
        await waitFor(() => expect(mockedApi.getMessages).toHaveBeenCalledTimes(2));
        expect(document.querySelector('[data-message-id="42"] .msg-assistant')).toBe(answer);
    });

    it('stays settled when an unconfirmed ending gets its saved id on refresh', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: QUESTION } });
            fireEvent.click(screen.getByLabelText('Send'));
        });

        // A synthesized done carries no message_id, so the answer starts on a temporary id.
        await act(async () => {
            callbacks!.onDone({ type: 'done', content: ANSWER } as StreamEvent);
        });
        // The refresh gives it its saved id; it keeps the settled mark made under the temporary one.
        await waitFor(() => expect(document.querySelector('[data-message-id="42"]')).not.toBeNull());
        const saved = document.querySelector('[data-message-id="42"]');
        expect(saved?.classList.contains('msg-settled')).toBe(true);
    });
});

describe('carryMessageKeys', () => {
    it('keys a server row by the shown message it replaces', () => {
        const keys = new Map<number, number>();
        carryMessageKeys([row(1700000000000, 'user', QUESTION), row(42, 'assistant', ANSWER)], [row(41, 'user', QUESTION), row(42, 'assistant', ANSWER)], keys);
        expect([...keys]).toEqual([[41, 1700000000000]]);
    });

    it('pairs repeated text one for one, and leaves new rows alone', () => {
        const keys = new Map<number, number>();
        carryMessageKeys(
            [row(-5, 'user', 'again'), row(-6, 'user', 'again')],
            [row(7, 'user', 'again'), row(8, 'user', 'again'), row(9, 'user', 'other')],
            keys,
        );
        expect([...keys]).toEqual([[7, -5], [8, -6]]);
    });
});
