// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
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
    conversation_starters: [
        'Find contract clauses',
        'Summarize this folder',
    ],
    models: [],
    tags: ['rag', 'files'],
    icon: 'file-text',
    tools_count: 3,
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

    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true,
        value: vi.fn(),
    });

    useChatStore.setState({
        agents: [DOC_AGENT],
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

describe('ChatView agent metadata', () => {
    it('uses the active agent metadata for the empty state and header indicator', async () => {
        render(<ChatView sessionId={SESSION.id} />);

        expect(await screen.findByRole('heading', { name: 'Doc Agent' })).toBeInTheDocument();
        expect(screen.getByText('Search and answer questions from indexed documents.')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Find contract clauses' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Summarize this folder' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Summarize a document' })).not.toBeInTheDocument();

        const capabilitySummaries = screen.getAllByText('3 tools | rag, files');
        expect(capabilitySummaries.length).toBeGreaterThan(0);

        const headerIndicator = screen.getByLabelText('Active agent');
        expect(within(headerIndicator).getByText('Doc Agent')).toBeInTheDocument();
        expect(within(headerIndicator).getByText('3 tools | rag, files')).toBeInTheDocument();
    });
});
