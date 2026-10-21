// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The agent's task list is pinned above the composer, not added to the steps. */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
import type { Session, StreamEvent, TaskListState } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const NOW = Date.now() / 1000;

const LIST: TaskListState = {
    items: [
        { id: 1, content: 'Read the config', status: 'completed', started_at: NOW - 30, completed_at: NOW - 20 },
        { id: 2, content: 'Fix the bug', status: 'in_progress', started_at: NOW - 20, completed_at: null },
    ],
    source: 'agent',
    current_action: 'Running command: pytest',
    action_started_at: NOW - 5,
    updated_at: NOW,
};

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

beforeEach(() => {
    vi.clearAllMocks();
    capturedCallbacks = null;
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.cancelStream.mockResolvedValue(undefined as never);
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        capturedCallbacks = cbs as api.StreamCallbacks;
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
        liveTaskLists: {},
    });
    useNotificationStore.setState({ notifications: [], showPanel: false, typeFilter: null });
});

describe('ChatView task list', () => {
    it('has no card for a chat without a list', () => {
        render(<ChatView sessionId={SESSION.id} />);
        expect(screen.queryByTestId('task-list-card')).toBeNull();
    });

    it('pins a streamed list above the composer and keeps it out of the steps', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'fix the bug' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        act(() => {
            capturedCallbacks!.onAgentEvent({ type: 'task_list', ...LIST } as unknown as StreamEvent);
        });

        const card = await screen.findByTestId('task-list-card');
        expect(card.closest('.input-column')).not.toBeNull();
        expect(screen.getAllByRole('listitem').map((li) => li.textContent?.split(',')[0])).toEqual([
            'Read the config',
            'Fix the bug',
        ]);
        expect(screen.getByTestId('task-list-action').textContent).toMatch(/^Running command: pytest · \d+s$/);
        expect(useChatStore.getState().agentSteps).toEqual([]);
    });

    it('keeps the last good list when a malformed event arrives', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'go' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        act(() => {
            capturedCallbacks!.onAgentEvent({ type: 'task_list', ...LIST } as unknown as StreamEvent);
            capturedCallbacks!.onAgentEvent({ type: 'task_list', items: 'garbage' } as unknown as StreamEvent);
            capturedCallbacks!.onAgentEvent({ type: 'task_list', items: [{ content: 7, status: 'x' }] } as unknown as StreamEvent);
        });
        await screen.findByTestId('task-list-card');
        expect(screen.getAllByRole('listitem')).toHaveLength(2);
    });

    it('takes the card down once the chat\'s list is cleared (as a resend does)', async () => {
        useChatStore.setState({
            liveTaskLists: { [SESSION.id]: LIST },
            sessions: [{ ...SESSION, task_list: LIST }],
        });
        useChatStore.getState().clearTaskList(SESSION.id);
        render(<ChatView sessionId={SESSION.id} />);
        expect(screen.queryByTestId('task-list-card')).toBeNull();
    });

    it('shows the list saved with the session when it is reopened', async () => {
        useChatStore.setState({
            sessions: [{ ...SESSION, task_list: { ...LIST, current_action: null, action_started_at: null } }],
        });
        render(<ChatView sessionId={SESSION.id} />);
        const card = await waitFor(() => screen.getByTestId('task-list-card'));
        expect(card.querySelector('.task-list-head')?.getAttribute('aria-expanded')).toBe('false');
        expect(card.textContent).toContain('1 of 2');
    });
});
