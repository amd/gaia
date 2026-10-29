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
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
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
    const view = render(<ChatView sessionId={SESSION.id} />);

    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), {
            target: { value: 'send the drafted email' },
        });
        fireEvent.click(screen.getByLabelText('Send'));
    });

    expect(capturedCallbacks).not.toBeNull();
    return view;
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
            await waitFor(() => expect(attachCallbacks.length).toBe(i + 1), { timeout: 12_000 });
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
        act(() => capturedCallbacks!.onError(new api.StreamTimeoutError('send', api.SEND_HEADER_TIMEOUT_MS)));
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

    it('reconnects after the connection is cut mid-answer', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onChunk({ type: 'chunk', content: 'Partial answer' }));
        act(() => capturedCallbacks!.onError(new api.StreamDroppedError(new TypeError('network error'))));
        expect(mockedApi.attachToRun).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(document.body.textContent).toContain('Reconnecting (attempt 1 of 3)'));
        expect(document.body.textContent).not.toContain('send your message again');
    });

    it('waits before a later reconnect, so a restarting backend has time to come back', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onError(new api.StreamDroppedError(new TypeError('network error'))));
        act(() => attachCallbacks[0].onError(new api.StreamDroppedError(new TypeError('Failed to fetch'))));
        expect(mockedApi.attachToRun).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(document.body.textContent).toContain('Reconnecting (attempt 2 of 3)'));
        await waitFor(() => expect(mockedApi.attachToRun).toHaveBeenCalledTimes(2), { timeout: 5_000 });
    });

    it('does not bring back a prompt the user already answered when a reconnect replays it', async () => {
        await driveSend();
        const prompt = { type: 'permission_request', tool: 'write_file', confirm_id: 'c1', args: {} } as never;
        act(() => capturedCallbacks!.onAgentEvent(prompt));
        useNotificationStore.setState((st) => ({
            notifications: st.notifications.map((n) => (n.id === 'c1' ? { ...n, response: 'allow', read: true } : n)),
        }));
        act(() => capturedCallbacks!.onError(new api.StreamStalledError(30_000)));
        act(() => attachCallbacks[0].onAgentEvent(prompt));
        const copies = useNotificationStore.getState().notifications.filter((n) => n.id === 'c1');
        expect(copies).toHaveLength(1);
        expect(copies[0].response).toBe('allow');
    });

    it('clears a replayed prompt that the run already moved past', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onError(new api.StreamStalledError(30_000)));
        act(() => attachCallbacks[0].onAgentEvent({ type: 'permission_request', tool: 'write_file', confirm_id: 'c2', args: {} } as never));
        act(() => attachCallbacks[0].onAgentEvent({ type: 'tool_start', tool: 'write_file' } as never));
        const prompt = useNotificationStore.getState().notifications.find((n) => n.id === 'c2');
        expect(prompt?.dismissed).toBe(true);
    });

    it('clears the waiting notice as soon as the backend answers', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onAgentEvent({
            type: 'status', status: 'working', phase: 'waiting', message: 'Waiting for GAIA to start this answer…',
        }));
        await waitFor(() => expect(document.querySelector('.thinking-indicator-text')?.textContent)
            .toBe('Waiting for GAIA to start this answer…'));
        act(() => capturedCallbacks!.onConnected?.());
        await waitFor(() => expect(document.querySelector('.thinking-indicator-text')?.textContent)
            .not.toBe('Waiting for GAIA to start this answer…'));
    });

    it('closes a stopped turn whose stream dies instead of reconnecting it', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onChunk({ type: 'chunk', content: 'Partial answer' }));
        await act(async () => { fireEvent.click(screen.getByLabelText('Stop')); });
        act(() => capturedCallbacks!.onError(new api.StreamStalledError(30_000)));
        expect(mockedApi.attachToRun).not.toHaveBeenCalled();
        await waitFor(() => expect(useChatStore.getState().isStreaming).toBe(false));
        const msgs = useChatStore.getState().messages;
        const last = msgs[msgs.length - 1];
        expect(last?.stopState).toBe('unconfirmed');
        expect(last?.stopError).toBeUndefined();
    });

    it('loads the saved answer when the run finished just before the chat reopened', async () => {
        mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [SESSION.id], queued: [], awaiting_user: {} });
        useChatStore.setState({ runningSessionIds: [SESSION.id], activeRunsAt: Date.now() } as never);
        mockedApi.getMessages.mockResolvedValue({
            messages: [
                { id: 1, session_id: SESSION.id, role: 'user', content: 'send the drafted email', created_at: '', rag_sources: null },
                { id: 2, session_id: SESSION.id, role: 'assistant', content: 'Sent it.', created_at: '', rag_sources: null },
            ],
            total: 2,
        } as never);
        render(<ChatView sessionId={SESSION.id} />);
        await waitFor(() => expect(attachCallbacks).toHaveLength(1));
        act(() => attachCallbacks[0].onDone({ type: 'done', content: '' }));
        await waitFor(() => expect(document.body.textContent).toContain('Sent it.'));
        expect(document.body.textContent).not.toContain('was lost');
        expect(document.body.textContent).not.toContain('HTTP 404');
    });

    it('drops a pending reconnect when the user presses Stop during the wait', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onError(new api.StreamDroppedError(new TypeError('network error'))));
        act(() => attachCallbacks[0].onError(new api.StreamDroppedError(new TypeError('Failed to fetch'))));
        await act(async () => { fireEvent.click(screen.getByLabelText('Stop')); });
        await new Promise((r) => setTimeout(r, 3_500));
        expect(mockedApi.attachToRun).toHaveBeenCalledTimes(1);
    });

    it('shows the prompt the run is still waiting on after a reconnect replays earlier steps', async () => {
        await driveSend();
        const prompt = { type: 'permission_request', tool: 'write_file', confirm_id: 'c3', args: {} } as never;
        act(() => capturedCallbacks!.onAgentEvent({ type: 'tool_start', tool: 'read_file' } as never));
        act(() => capturedCallbacks!.onAgentEvent({ type: 'tool_end', tool: 'read_file' } as never));
        act(() => capturedCallbacks!.onAgentEvent(prompt));
        act(() => capturedCallbacks!.onError(new api.StreamStalledError(30_000)));
        // The replay starts from the top: earlier steps, then the prompt still open.
        act(() => attachCallbacks[0].onAgentEvent({ type: 'tool_start', tool: 'read_file' } as never));
        act(() => attachCallbacks[0].onAgentEvent({ type: 'tool_end', tool: 'read_file' } as never));
        act(() => attachCallbacks[0].onAgentEvent(prompt));
        const shown = useNotificationStore.getState().notifications.filter((n) => n.id === 'c3');
        expect(shown).toHaveLength(1);
        expect(shown[0].dismissed).toBe(false);
    });

    it('does not re-attach twice when the user leaves and comes back during a reconnect wait', async () => {
        const view = await driveSend();
        act(() => capturedCallbacks!.onError(new api.StreamDroppedError(new TypeError('network error'))));
        act(() => attachCallbacks[0].onError(new api.StreamDroppedError(new TypeError('Failed to fetch'))));
        act(() => useChatStore.setState({ currentSessionId: 'other' }));
        view.rerender(<ChatView sessionId="other" />);
        act(() => useChatStore.setState({ currentSessionId: SESSION.id }));
        view.rerender(<ChatView sessionId={SESSION.id} />);
        await new Promise((r) => setTimeout(r, 3_500));
        expect(mockedApi.attachToRun).toHaveBeenCalledTimes(1);
    });

    it('closes the turn at once when Stop lands during a reconnect wait', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onError(new api.StreamDroppedError(new TypeError('network error'))));
        act(() => attachCallbacks[0].onError(new api.StreamDroppedError(new TypeError('Failed to fetch'))));
        await act(async () => { fireEvent.click(screen.getByLabelText('Stop')); });
        await waitFor(() => expect(useChatStore.getState().isStreaming).toBe(false), { timeout: 1_000 });
    });

    it('clears the reconnecting notice once the re-attach connects', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onError(new api.StreamStalledError(30_000)));
        await waitFor(() => expect(document.body.textContent).toContain('Reconnecting (attempt 1 of 3)'));
        act(() => attachCallbacks[0].onConnected?.());
        await waitFor(() => expect(document.body.textContent).not.toContain('Reconnecting (attempt 1 of 3)'));
    });
    it('keeps a prompt still waiting for the user through a reconnect replay (review repro)', async () => {
        await driveSend();
        const prompt = { type: 'permission_request', tool: 'write_file', confirm_id: 'live1', args: {} } as never;
        act(() => capturedCallbacks!.onAgentEvent({ type: 'tool_start', tool: 'read_file' } as never));
        act(() => capturedCallbacks!.onAgentEvent({ type: 'tool_end', tool: 'read_file' } as never));
        act(() => capturedCallbacks!.onAgentEvent(prompt));
        act(() => capturedCallbacks!.onError(new api.StreamDroppedError(new TypeError('network error'))));
        // The replay starts from the top of the run.
        act(() => attachCallbacks[0].onAgentEvent({ type: 'tool_start', tool: 'read_file' } as never));
        act(() => attachCallbacks[0].onAgentEvent({ type: 'tool_end', tool: 'read_file' } as never));
        act(() => attachCallbacks[0].onAgentEvent(prompt));
        const n = useNotificationStore.getState().notifications.find((x) => x.id === 'live1');
        expect(n?.dismissed).not.toBe(true);
    });
    it('clears the reconnect notice once the re-attach streams again (review repro)', async () => {
        await driveSend();
        act(() => capturedCallbacks!.onError(new api.StreamStalledError(30_000)));
        act(() => attachCallbacks[0].onConnected?.());
        act(() => attachCallbacks[0].onChunk({ type: 'chunk', content: 'Answer text' }));
        expect(useChatStore.getState().liveStatus ?? '').not.toContain('Reconnecting');
    });
});
