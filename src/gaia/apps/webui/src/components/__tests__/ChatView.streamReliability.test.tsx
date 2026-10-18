// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A chat whose stream dies or whose backend is busy: the view reconnects
 * visibly and a bounded number of times, and errors read as plain words.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
import type { AgentInfo, Session } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
    const real = await importOriginal<typeof import('../../services/api')>();
    const mocked = Object.fromEntries(
        Object.entries(real).map(([k, v]) => [k, typeof v === 'function' && !/Error$/.test(k) ? vi.fn() : v]),
    );
    return mocked;
});

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
    agent_type: 'gaia',
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

let capturedCallbacks: api.StreamCallbacks | null = null;
let attachCallbacks: api.StreamCallbacks[] = [];

beforeEach(() => {
    vi.clearAllMocks();
    capturedCallbacks = null;
    attachCallbacks = [];
    mockedApi.attachToRun.mockImplementation((_sid, cbs) => {
        attachCallbacks.push(cbs);
        return new AbortController();
    });

    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
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
        fireEvent.change(screen.getByLabelText('Message'), {
            target: { value: 'send the drafted email' },
        });
        fireEvent.click(screen.getByLabelText('Send'));
    });

    expect(capturedCallbacks).not.toBeNull();
}


describe('ChatView stream reliability', () => {
    it('re-attaches a stream that went silent, and says it is reconnecting', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onChunk({ type: 'chunk', content: 'Partial answer' }));

        act(() => capturedCallbacks!.onError(new api.StreamStalledError(30_000)));

        expect(mockedApi.attachToRun).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(document.body.textContent).toContain('Reconnecting (attempt 1 of 3)'));
        expect(useChatStore.getState().isStreaming).toBe(true);
        expect(document.body.textContent).not.toContain('Lost the live view');

        // The replay brings the answer back and the turn ends normally.
        mockedApi.getMessages.mockResolvedValue({
            messages: [
                { id: 1, session_id: SESSION.id, role: 'user', content: 'send the drafted email', created_at: '', rag_sources: null },
                { id: 2, session_id: SESSION.id, role: 'assistant', content: 'Whole answer', created_at: '', rag_sources: null },
            ],
            total: 2,
        } as never);
        act(() => attachCallbacks[0].onChunk({ type: 'chunk', content: 'Whole answer' }));
        act(() => attachCallbacks[0].onDone({ type: 'done', content: 'Whole answer' }));
        await waitFor(() => expect(useChatStore.getState().isStreaming).toBe(false));
        await waitFor(() => expect(useChatStore.getState().messages.map((m) => m.id)).toEqual([1, 2]));
        await waitFor(() => expect(document.body.textContent).toContain('Whole answer'));
    });

    it('gives up after three reconnects with a plain-words message', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onError(new api.StreamStalledError(30_000)));
        for (let i = 0; i < 3; i++) {
            act(() => attachCallbacks[i].onError(new api.StreamStalledError(30_000)));
        }
        expect(mockedApi.attachToRun).toHaveBeenCalledTimes(3);
        await waitFor(() => expect(document.body.textContent).toContain('Lost the live view of this answer'));
        expect(useChatStore.getState().isStreaming).toBe(false);
    });

    it('explains a busy server in plain words instead of showing the raw response', async () => {
        await driveSend();
        const body = JSON.stringify({ detail: 'The server is busy processing other chat requests. Please try again in a few moments.' });
        act(() => capturedCallbacks!.onError(new api.ApiError(`HTTP 429: ${body}`, 429, body, null)));
        await waitFor(() => expect(document.body.textContent).toContain('GAIA is busy with another chat'));
        expect(document.body.textContent).not.toContain('HTTP 429');
        expect(document.body.textContent).not.toContain('{"detail"');
    });

    it('explains a send the backend never started', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onError(new api.StreamTimeoutError(api.SEND_HEADER_TIMEOUT_MS)));
        await waitFor(() => expect(document.body.textContent).toContain('did not start this answer within 90 seconds'));
        expect(document.body.textContent).not.toContain('Try a shorter question');
    });

    it('does not re-attach a turn that already ended', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onDone({ type: 'done', content: 'Done.' }));
        act(() => capturedCallbacks!.onError(new api.StreamStalledError(30_000)));
        expect(mockedApi.attachToRun).not.toHaveBeenCalled();
        expect(useChatStore.getState().isStreaming).toBe(false);
    });

    it('says the answer was lost when a reconnect finds neither the run nor a saved answer', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onError(new api.StreamStalledError(30_000)));
        // The backend restarted: no run to attach to, and only the question was saved.
        mockedApi.getMessages.mockResolvedValue({
            messages: [{ id: 1, session_id: SESSION.id, role: 'user', content: 'send the drafted email', created_at: '', rag_sources: null }],
            total: 1,
        } as never);
        act(() => attachCallbacks[0].onDone({ type: 'done', content: '' }));
        await waitFor(() => expect(document.body.textContent).toContain('The answer to your last message was lost'));
    });

    it('shows the wait for the backend live, without saving it as a step of the answer', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onAgentEvent({
            type: 'status', status: 'working', phase: 'waiting', message: 'Waiting for GAIA to start this answer…',
        }));
        await waitFor(() => expect(document.querySelector('.thinking-indicator-text')?.textContent)
            .toBe('Waiting for GAIA to start this answer…'));
        expect(useChatStore.getState().agentSteps).toEqual([]);
    });
});
