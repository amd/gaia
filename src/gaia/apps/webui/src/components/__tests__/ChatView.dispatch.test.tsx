// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, fireEvent, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { AgentInfo, Session } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-email',
    title: 'New Task',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'gemma',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    // Session is pinned to email, even though the globally-active agent is chat.
    agent_type: 'email',
};

const CHAT_AGENT: AgentInfo = {
    id: 'chat', name: 'Chat', description: '', source: 'builtin',
    conversation_starters: [], models: [],
};
const EMAIL_AGENT: AgentInfo = {
    id: 'email', name: 'Email', description: '', source: 'installed',
    conversation_starters: [], models: [],
};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [], total: 0, total_size_bytes: 0, total_chunks: 0,
    });
    mockedApi.sendMessageStream.mockImplementation(() => new AbortController());

    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true,
        value: vi.fn(),
    });

    useChatStore.setState({
        agents: [CHAT_AGENT, EMAIL_AGENT],
        activeAgentId: 'chat', // global selection differs from the session's
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

afterEach(() => vi.useRealTimers());

describe('ChatView per-session dispatch (#2179)', () => {
    it('dispatches to the session-pinned agent, not the globally-active one', async () => {
        render(<ChatView sessionId={SESSION.id} />);

        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message input'), {
                target: { value: 'triage my inbox' },
            });
            fireEvent.click(screen.getByLabelText('Send message'));
        });

        expect(mockedApi.sendMessageStream).toHaveBeenCalledTimes(1);
        // 6th positional arg is the agent_type dispatch target.
        const agentTypeArg = mockedApi.sendMessageStream.mock.calls[0][5];
        expect(agentTypeArg).toBe('email');
    });
});
