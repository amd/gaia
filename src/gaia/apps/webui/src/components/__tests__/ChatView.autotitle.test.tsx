// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// #2165: explicit renames must PIN the session title (title_is_custom: true)
// while the client-side first-message auto-title must NOT pin it
// (title_is_custom: false) so the server-side LLM titler can still improve it.

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { AgentInfo, Session } from '../../types';
import * as api from '../../services/api';
import type { StreamEvent } from '../../types';

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

let capturedCallbacks: api.StreamCallbacks | null = null;

function setSession(session: Session) {
    useChatStore.setState({
        agents: [DOC_AGENT],
        activeAgentId: 'doc',
        sessions: [session],
        currentSessionId: session.id,
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
}

beforeEach(() => {
    vi.clearAllMocks();
    capturedCallbacks = null;

    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [],
        total: 0,
        total_size_bytes: 0,
        total_chunks: 0,
    });
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        capturedCallbacks = cbs as api.StreamCallbacks;
        return new AbortController();
    });

    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true,
        value: vi.fn(),
    });

    setSession(SESSION);
});

describe('ChatView title pinning (#2165)', () => {
    it('client-side auto-title on first message does NOT pin the title', async () => {
        render(<ChatView sessionId={SESSION.id} />);

        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message input'), {
                target: { value: 'what is the capital of france' },
            });
            fireEvent.click(screen.getByLabelText('Send message'));
        });
        expect(capturedCallbacks).not.toBeNull();

        await act(async () => {
            capturedCallbacks!.onDone({
                type: 'done',
                content: 'Paris.',
            } as unknown as StreamEvent);
        });

        expect(mockedApi.updateSession).toHaveBeenCalledWith(SESSION.id, {
            title: 'what is the capital of france',
            title_is_custom: false,
        });
    });

    it('manual rename pins the title', async () => {
        setSession({ ...SESSION, title: 'Auto Generated Title' });
        render(<ChatView sessionId={SESSION.id} />);

        fireEvent.click(screen.getByLabelText('Rename task'));
        const input = screen.getByLabelText('Edit task title');
        await act(async () => {
            fireEvent.change(input, { target: { value: 'My Research Project' } });
            fireEvent.keyDown(input, { key: 'Enter' });
        });

        expect(mockedApi.updateSession).toHaveBeenCalledWith(SESSION.id, {
            title: 'My Research Project',
            title_is_custom: true,
        });
    });
});
