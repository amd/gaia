// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A turn the model server ended by being down offers the fix under the reply (#6695). */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { LocalModelStatus, Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1',
    title: 'Existing chat',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'fireworks.glm-5',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'gaia',
};

const STOPPED = {
    state: 'stopped', owner: 'gaia', restarting: false, restart_error: null,
} as unknown as LocalModelStatus;

const REPLY = 'The model call failed at step 1, so this request is unfinished. '
    + "The local model server at 127.0.0.1:13305 isn't answering, so the message never reached Fireworks AI.";

let callbacks: api.StreamCallbacks | null = null;

beforeEach(() => {
    vi.clearAllMocks();
    callbacks = null;
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.getLocalModelStatus.mockResolvedValue(STOPPED);
    mockedApi.getLemonadeTarget.mockResolvedValue({ mode: 'gaia', source: 'gaia' } as never);
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        callbacks = cbs as api.StreamCallbacks;
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
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
        locale: 'en',
    });
});

async function send() {
    render(<ChatView sessionId={SESSION.id} />);
    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'hi' } });
        fireEvent.click(screen.getByLabelText('Send'));
    });
    expect(callbacks).not.toBeNull();
}

describe('ChatView model server down', () => {
    it('offers to start the server under the failed reply, and keeps it after the refetch', async () => {
        await send();
        mockedApi.getMessages.mockResolvedValue({
            messages: [
                { id: 6, session_id: SESSION.id, role: 'user', content: 'hi', created_at: '2026-07-16T00:00:00.000Z', rag_sources: null },
                { id: 7, session_id: SESSION.id, role: 'assistant', content: REPLY, created_at: '2026-07-16T00:00:01.000Z', rag_sources: null },
            ],
            total: 2,
        } as never);
        await act(async () => {
            callbacks!.onDone({
                type: 'done', content: REPLY, message_id: 7, failed: true, failure: 'model_server_down',
            } as StreamEvent);
        });
        expect(await screen.findByRole('button', { name: 'Start the model server' })).toBeInTheDocument();
        // The refetch swaps in the server's rows, which carry no failure.
        await waitFor(() => expect(mockedApi.getMessages).toHaveBeenCalled());
        await waitFor(() => expect(useChatStore.getState().messages.map((m) => m.id)).toEqual([6, 7]));
        expect(document.querySelectorAll('.server-down-notice')).toHaveLength(1);
    });

    it('offers nothing for another failure', async () => {
        await send();
        await act(async () => {
            callbacks!.onDone({ type: 'done', content: 'Rate limited.', message_id: 8, failed: true } as StreamEvent);
        });
        await waitFor(() => expect(useChatStore.getState().isStreaming).toBe(false));
        expect(document.querySelector('.server-down-notice')).toBeNull();
    });
});
