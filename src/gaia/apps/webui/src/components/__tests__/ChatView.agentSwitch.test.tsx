// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
import type { AgentInfo, Session } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1',
    title: 'New Task',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'doc',
};

const DOC_AGENT: AgentInfo = {
    id: 'doc',
    name: 'Doc Agent',
    description: 'Search and answer questions from indexed documents.',
    source: 'builtin',
    conversation_starters: [],
    models: [],
    tags: ['rag', 'files'],
    icon: 'file-text',
    tools_count: 3,
};

const CHAT_AGENT: AgentInfo = {
    id: 'chat',
    name: 'Chat Agent',
    description: 'General chat agent.',
    source: 'builtin',
    conversation_starters: [],
    models: [],
    tags: [],
    icon: 'message',
    tools_count: 0,
};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [],
        total: 0,
        total_size_bytes: 0,
        total_chunks: 0,
    });
    mockedApi.updateSession.mockResolvedValue(SESSION);

    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true,
        value: vi.fn(),
    });

    useNotificationStore.setState({ notifications: [], alwaysAllowGrants: [], showPanel: false });
    useChatStore.setState({
        agents: [DOC_AGENT, CHAT_AGENT],
        activeAgentId: 'doc',
        sessions: [SESSION],
        currentSessionId: SESSION.id,
        messages: [],
        documents: [],
        isStreaming: false,
        streamingContent: '',
        agentSteps: [],
        isLoadingMessages: false,
        pendingPrompt: null,
        systemStatus: null,
    });
});

// Verbatim from `_reject_unknown_agent_type` in src/gaia/ui/routers/sessions.py —
// asserting on a message the server cannot send would make this test green on a
// scenario that never happens.
const SERVER_REJECTION =
    "Unknown agent_type 'chat'. Registered agent ids: builder, chat, doc, email, file, gaia. "
    + 'Pick one of these, or install the agent from the Agent Hub (`gaia hub`) and restart the server.';

async function switchToChatAgent() {
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Switch agent' }));
    await user.click(await screen.findByRole('button', { name: /Chat Agent/i }));
}

describe('ChatView agent switch failures', () => {
    it('shows the backend detail when switching agent on an empty chat is rejected', async () => {
        mockedApi.updateSession.mockRejectedValueOnce(new Error(SERVER_REJECTION));

        render(<ChatView sessionId={SESSION.id} />);
        await switchToChatAgent();

        await waitFor(() => {
            const notes = useNotificationStore.getState().notifications;
            expect(notes).toHaveLength(1);
            expect(notes[0].type).toBe('error');
            expect(notes[0].message).toBe(SERVER_REJECTION);
            expect(notes[0].title).toBe('Agent switch failed');
        });

        // The reason has to be on screen, not just behind the unread badge.
        expect(useNotificationStore.getState().showPanel).toBe(true);
        expect(useChatStore.getState().activeAgentId).toBe('doc');
        expect(useChatStore.getState().sessions[0].agent_type).toBe('doc');
    });

    it('gives each failure a distinct notification id', async () => {
        // Pin the clock so two failures genuinely land in the same millisecond.
        const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000);
        mockedApi.updateSession.mockRejectedValue(new Error(SERVER_REJECTION));

        render(<ChatView sessionId={SESSION.id} />);
        await switchToChatAgent();
        await waitFor(() => expect(useNotificationStore.getState().notifications).toHaveLength(1));
        await switchToChatAgent();
        await waitFor(() => expect(useNotificationStore.getState().notifications).toHaveLength(2));

        const ids = useNotificationStore.getState().notifications.map((n) => n.id);
        expect(new Set(ids).size).toBe(2);
        nowSpy.mockRestore();
    });
});
