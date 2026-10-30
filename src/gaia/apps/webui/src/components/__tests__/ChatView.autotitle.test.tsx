// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// #2165: explicit renames must PIN the session title (title_is_custom: true).
// A new chat's first-message name comes from the server, which leaves it
// unpinned so its LLM titler can still improve it; the view only shows it.

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { AgentInfo, Session } from '../../types';
import * as api from '../../services/api';
import { MAX_SESSION_TITLE_CHARS } from '../../utils/constants';
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
    agent_type: 'gaia',
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
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
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
    it('shows the name the server gave a new chat, without writing one itself', async () => {
        mockedApi.getSession.mockResolvedValue({ ...SESSION, title: 'what is the capital of france' });
        render(<ChatView sessionId={SESSION.id} />);

        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), {
                target: { value: 'what is the capital of france' },
            });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        expect(capturedCallbacks).not.toBeNull();

        // The first event means the server took the message, and named the chat.
        await act(async () => {
            capturedCallbacks!.onAgentEvent({ type: 'status', message: 'Connecting to LLM...' } as unknown as StreamEvent);
            capturedCallbacks!.onChunk({ type: 'chunk', content: 'Par' } as unknown as StreamEvent);
            capturedCallbacks!.onDone({
                type: 'done',
                content: 'Paris.',
            } as unknown as StreamEvent);
        });

        expect(mockedApi.getSession).toHaveBeenCalledTimes(1);
        expect(mockedApi.getSession).toHaveBeenCalledWith(SESSION.id);
        expect(useChatStore.getState().sessions[0].title).toBe('what is the capital of france');
        // A client-written title would block the server's titler from improving it.
        expect(mockedApi.updateSession).not.toHaveBeenCalled();
    });

    it('leaves a named chat alone', async () => {
        setSession({ ...SESSION, title: 'Capital cities' });
        render(<ChatView sessionId={SESSION.id} />);

        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'and of spain?' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        await act(async () => {
            capturedCallbacks!.onChunk({ type: 'chunk', content: 'Madrid' } as unknown as StreamEvent);
        });

        expect(mockedApi.getSession).not.toHaveBeenCalled();
        expect(mockedApi.updateSession).not.toHaveBeenCalled();
    });

    it.each(['New Chat', 'Untitled', ''])('refreshes a chat the server still treats as unnamed (%j)', async (title) => {
        setSession({ ...SESSION, title });
        mockedApi.getSession.mockResolvedValue({ ...SESSION, title: 'plan a trip' });
        render(<ChatView sessionId={SESSION.id} />);

        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'plan a trip' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        await act(async () => {
            capturedCallbacks!.onChunk({ type: 'chunk', content: 'Sure' } as unknown as StreamEvent);
        });

        expect(mockedApi.getSession).toHaveBeenCalledTimes(1);
        expect(useChatStore.getState().sessions[0].title).toBe('plan a trip');
    });

    it('manual rename pins the title', async () => {
        setSession({ ...SESSION, title: 'Auto Generated Title' });
        render(<ChatView sessionId={SESSION.id} />);

        fireEvent.click(screen.getByRole('button', { name: 'Auto Generated Title' }));
        const input = screen.getByLabelText('Chat title');
        await act(async () => {
            fireEvent.change(input, { target: { value: 'My Research Project' } });
            fireEvent.keyDown(input, { key: 'Enter' });
        });

        expect(mockedApi.updateSession).toHaveBeenCalledWith(SESSION.id, {
            title: 'My Research Project',
            title_is_custom: true,
        });
    });

    it('caps a rename at the backend title limit (#5257)', async () => {
        setSession({ ...SESSION, title: 'Auto Generated Title' });
        render(<ChatView sessionId={SESSION.id} />);

        fireEvent.click(screen.getByRole('button', { name: 'Auto Generated Title' }));
        const input = screen.getByLabelText('Chat title');
        expect(input).toHaveAttribute('maxLength', String(MAX_SESSION_TITLE_CHARS));
        await act(async () => {
            fireEvent.change(input, { target: { value: 'x'.repeat(100_000) } });
            fireEvent.keyDown(input, { key: 'Enter' });
        });

        expect(mockedApi.updateSession).toHaveBeenCalledWith(SESSION.id, {
            title: 'x'.repeat(MAX_SESSION_TITLE_CHARS),
            title_is_custom: true,
        });
    });

    it('leaves an old over-long title alone when the rename is not edited', async () => {
        const longTitle = 'y'.repeat(MAX_SESSION_TITLE_CHARS + 50);
        setSession({ ...SESSION, title: longTitle });
        render(<ChatView sessionId={SESSION.id} />);

        fireEvent.click(screen.getByRole('button', { name: longTitle }));
        await act(async () => {
            fireEvent.blur(screen.getByLabelText('Chat title'));
        });

        expect(mockedApi.updateSession).not.toHaveBeenCalled();
    });
});
