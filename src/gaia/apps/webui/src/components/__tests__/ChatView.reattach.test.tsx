// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A chat waiting on a permission prompt keeps it across leaving the chat and
 * coming back, and across a reload (#5247).
 */

import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
import type { Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

function chat(id: string): Session {
    return {
        id, title: `Chat ${id}`, created_at: '2026-06-10T00:00:00Z', updated_at: '2026-06-10T00:00:00Z',
        model: 'qwen', system_prompt: null, message_count: 1, document_ids: [], agent_type: 'gaia',
    };
}

const PROMPT = {
    type: 'permission_request',
    tool: 'write_file',
    args: { file_path: 'C:/notes/report.txt' },
    confirm_id: 'confirm-1',
} as unknown as StreamEvent;

/** Every attach of chat "a" replays the pending prompt, as the server's buffer does. */
let attaches: Record<string, api.StreamCallbacks[]>;

beforeEach(() => {
    vi.clearAllMocks();
    attaches = {};
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getMessageCount.mockResolvedValue(0 as never);
    mockedApi.getActiveRuns.mockResolvedValue({
        session_ids: ['a', 'b'], queued: ['b'], awaiting_user: { a: 'permission' },
    });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'a', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.attachToRun.mockImplementation((sid, cbs) => {
        (attaches[sid] ??= []).push(cbs as api.StreamCallbacks);
        if (sid === 'a') queueMicrotask(() => (cbs as api.StreamCallbacks).onAgentEvent(PROMPT));
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        sessions: [chat('a'), chat('b')], currentSessionId: 'a', messages: [], documents: [],
        isStreaming: false, streamingContent: '', agentSteps: [], cards: [], isLoadingMessages: false,
        pendingPrompt: null, systemStatus: null,
        // The app-wide poll has already seen both runs.
        runningSessionIds: ['a', 'b'], queuedSessionIds: ['b'], awaitingBySession: { a: 'permission' },
        activeRunsAt: Date.now(),
    });
    useNotificationStore.setState({ notifications: [], showPanel: false, typeFilter: null });
});

/** What App does on a chat switch: a new keyed ChatView, then a streaming reset. */
function open(rerender: (ui: React.ReactElement) => void, id: string) {
    act(() => useChatStore.setState({ currentSessionId: id }));
    rerender(<ChatView key={id} sessionId={id} />);
    act(() => useChatStore.getState().resetStreaming());
}

describe('a pending permission prompt (#5247)', () => {
    it('is shown when the chat is opened fresh, as after a reload', async () => {
        render(<ChatView key="a" sessionId="a" />);
        expect(await screen.findByRole('button', { name: /Allow once/ })).toBeInTheDocument();
        expect(screen.getByLabelText('Stop')).toBeInTheDocument();
        expect(attaches.a).toHaveLength(1);
    });

    it('comes back after leaving for a chat that is still streaming, and can be stopped', async () => {
        const { rerender } = render(<ChatView key="a" sessionId="a" />);
        await screen.findByRole('button', { name: /Allow once/ });

        // Chat "b" is queued behind it and streams its wait status.
        open(rerender, 'b');
        await waitFor(() => expect(attaches.b).toHaveLength(1));
        expect(useChatStore.getState().isStreaming).toBe(true);

        // Back to "a": the global streaming flag is still b's until App resets it.
        open(rerender, 'a');
        expect(await screen.findByRole('button', { name: /Allow once/ })).toBeInTheDocument();
        expect(screen.getByLabelText('Stop')).toBeInTheDocument();
        expect(attaches.a).toHaveLength(2);

        // The replay doesn't pile up copies of the same prompt in the bell.
        const prompts = useNotificationStore.getState().notifications.filter((n) => n.id === 'confirm-1');
        expect(prompts).toHaveLength(1);
        expect(prompts[0].dismissed).toBe(false);
    });

    it('does not re-attach a run that finished in this view', async () => {
        render(<ChatView key="a" sessionId="a" />);
        await screen.findByRole('button', { name: /Allow once/ });
        // The run ends; a poll sent before the end still lists it as running.
        const staleAt = Date.now() - 1;
        act(() => attaches.a[0].onDone({ type: 'done', message_id: 7, content: 'Done.' } as unknown as StreamEvent));
        act(() => useChatStore.getState().setActiveRuns(
            { session_ids: ['a'], queued: [], awaiting_user: {} }, staleAt,
        ));
        await act(async () => {});
        expect(attaches.a).toHaveLength(1);
    });
});
