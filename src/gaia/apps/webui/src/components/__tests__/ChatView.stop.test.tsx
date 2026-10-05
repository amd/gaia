// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Stop keeps the stopped turn visible. Pressing Stop mid-run must not wipe
 * the steps the agent already took: the client keeps reading the stream until
 * the server's closing `done`, then shows the turn with a stopped marker.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView, STOP_GRACE_MS } from '../ChatView';
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

let capturedCallbacks: api.StreamCallbacks | null = null;
let controller: AbortController;

beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    capturedCallbacks = null;
    controller = new AbortController();

    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getMessageCount.mockResolvedValue(0);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [], total: 0, total_size_bytes: 0, total_chunks: 0,
    });
    mockedApi.cancelStream.mockResolvedValue({ cancelled: true });
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        capturedCallbacks = cbs as api.StreamCallbacks;
        return controller;
    });

    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true,
        value: vi.fn(),
    });

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
        runningSessionIds: [],
    });
    useNotificationStore.setState({ notifications: [], showPanel: false, typeFilter: null });
});

afterEach(() => {
    vi.useRealTimers();
});

async function sendAndRunTools() {
    render(<ChatView sessionId={SESSION.id} />);
    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), {
            target: { value: 'delete every .tmp file in Downloads' },
        });
        fireEvent.click(screen.getByLabelText('Send'));
    });
    expect(capturedCallbacks).not.toBeNull();
    // The sidebar's active-run poll has picked the run up.
    act(() => {
        useChatStore.getState().setRunningSessions([SESSION.id]);
    });
    // A tools-only turn: no answer text has streamed yet.
    act(() => {
        capturedCallbacks!.onAgentEvent({ type: 'tool_start', tool: 'run_shell_command' } as unknown as StreamEvent);
    });
    act(() => {
        capturedCallbacks!.onAgentEvent({ type: 'tool_result', summary: 'Listed 3 files', success: true } as unknown as StreamEvent);
    });
    act(() => {
        capturedCallbacks!.onAgentEvent({ type: 'tool_start', tool: 'run_shell_command' } as unknown as StreamEvent);
    });
}

function assistantMessage() {
    return useChatStore.getState().messages.find((m) => m.role === 'assistant');
}

describe('ChatView Stop', () => {
    it('keeps the in-progress steps visible while waiting for the server to close the turn', async () => {
        await sendAndRunTools();

        act(() => {
            fireEvent.click(screen.getByLabelText('Stop'));
        });

        expect(mockedApi.cancelStream).toHaveBeenCalledWith(SESSION.id);
        expect(controller.signal.aborted).toBe(false);
        expect(useChatStore.getState().isStreaming).toBe(true);
        expect(useChatStore.getState().agentSteps).toHaveLength(2);
        expect(screen.getByLabelText('Stop')).toBeDisabled();
    });

    it("finalizes the stopped turn from the server's done event, steps and marker included", async () => {
        await sendAndRunTools();

        act(() => {
            fireEvent.click(screen.getByLabelText('Stop'));
        });
        // What the backend persisted for the stopped turn; the post-done refetch reads it.
        mockedApi.getMessages.mockResolvedValue({
            messages: [
                { id: 41, session_id: SESSION.id, role: 'user', content: 'delete every .tmp file in Downloads', created_at: '2026-10-05T08:40:00Z', rag_sources: null },
                {
                    id: 42, session_id: SESSION.id, role: 'assistant', content: 'Cancelled.', created_at: '2026-10-05T08:40:15Z', rag_sources: null,
                    agent_steps: useChatStore.getState().agentSteps.map((s) => ({ ...s, active: false })),
                },
            ],
            total: 2,
        } as never);
        act(() => {
            capturedCallbacks!.onDone({ type: 'done', message_id: 42, content: 'Cancelled.' } as unknown as StreamEvent);
        });
        act(() => {
            vi.advanceTimersByTime(400);
        });
        await act(async () => {});

        const msg = assistantMessage();
        expect(msg?.id).toBe(42);
        expect(msg?.stopState).toBe('stopped');
        expect(msg?.agentSteps).toHaveLength(2);
        expect(msg?.agentSteps?.every((s) => !s.active)).toBe(true);
        expect(useChatStore.getState().isStreaming).toBe(false);
        expect(useChatStore.getState().runningSessionIds).not.toContain(SESSION.id);
        expect(screen.getByRole('note')).toHaveTextContent('Stopped');
        // The server's placeholder text is replaced by the marker, not shown twice.
        expect(screen.queryByText('Cancelled.')).toBeNull();
    });

    it('closes the turn locally, labelled unconfirmed, if the server never sends done', async () => {
        await sendAndRunTools();

        act(() => {
            fireEvent.click(screen.getByLabelText('Stop'));
        });
        act(() => {
            vi.advanceTimersByTime(STOP_GRACE_MS);
        });
        act(() => {
            vi.advanceTimersByTime(400);
        });
        await act(async () => {});

        const msg = assistantMessage();
        expect(msg?.stopState).toBe('unconfirmed');
        expect(msg?.agentSteps).toHaveLength(2);
        expect(controller.signal.aborted).toBe(true);
        expect(useChatStore.getState().isStreaming).toBe(false);
        expect(screen.getByRole('note')).toHaveTextContent('did not confirm the stop');

        // A late done must not add a second copy of the turn.
        act(() => {
            capturedCallbacks!.onDone({ type: 'done', message_id: 42, content: 'Cancelled.' } as unknown as StreamEvent);
        });
        expect(useChatStore.getState().messages.filter((m) => m.role === 'assistant')).toHaveLength(1);
    });

    it('keeps the steps when the stopped turn closes with an error instead of done', async () => {
        await sendAndRunTools();

        act(() => {
            fireEvent.click(screen.getByLabelText('Stop'));
        });
        act(() => {
            capturedCallbacks!.onError(new Error('run failed while stopping'));
        });

        const msg = assistantMessage();
        expect(msg?.content).toContain('run failed while stopping');
        expect(msg?.agentSteps).toHaveLength(2);
        expect(msg?.agentSteps?.every((s) => !s.active)).toBe(true);
        expect(useChatStore.getState().isStreaming).toBe(false);
        expect(screen.getByLabelText('Send')).toBeInTheDocument();

        // The grace timer was cleared: no second, unconfirmed copy of the turn.
        act(() => {
            vi.advanceTimersByTime(STOP_GRACE_MS);
        });
        expect(useChatStore.getState().messages.filter((m) => m.role === 'assistant')).toHaveLength(1);
    });

    it('a normal done without Stop carries no stopped marker', async () => {
        await sendAndRunTools();

        act(() => {
            capturedCallbacks!.onDone({ type: 'done', message_id: 7, content: 'Deleted 3 files.' } as unknown as StreamEvent);
        });

        expect(assistantMessage()?.stopState).toBeUndefined();
        expect(screen.queryByRole('note')).toBeNull();
    });
});
