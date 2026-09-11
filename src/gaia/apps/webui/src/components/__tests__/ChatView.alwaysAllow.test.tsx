// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * ChatView's client-side auto-approve for `tool_confirm`: only an explicit
 * "allow for the rest of this chat" grant skips the prompt, only in the chat
 * that granted it, and a one-off (remember=false) allow never does.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
import type { AgentInfo, Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');
const mockedApi = vi.mocked(api);

function session(id: string, title: string): Session {
    return {
        id,
        title,
        created_at: '2026-06-10T00:00:00Z',
        updated_at: '2026-06-10T00:00:00Z',
        model: 'qwen',
        system_prompt: null,
        message_count: 0,
        document_ids: [],
        agent_type: 'doc',
    };
}
const CHAT_A = session('chat-A', 'Chat A');
const CHAT_B = session('chat-B', 'Chat B');

const DOC_AGENT: AgentInfo = {
    id: 'doc',
    name: 'Doc Agent',
    description: 'Search and answer questions from indexed documents.',
    source: 'builtin',
    conversation_starters: [],
    models: [],
    tags: [],
    icon: 'file-text',
    tools_count: 3,
};

const toolConfirm = (confirmId: string, tool = 'run_shell_command') =>
    ({ type: 'tool_confirm', confirm_id: confirmId, tool, args: { command: 'dir' } }) as unknown as StreamEvent;

let captured: api.StreamCallbacks | null = null;

beforeEach(() => {
    vi.clearAllMocks();
    captured = null;
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [],
        total: 0,
        total_size_bytes: 0,
        total_chunks: 0,
    });
    mockedApi.cancelStream.mockResolvedValue(undefined as never);
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.confirmTool.mockResolvedValue(undefined as never);
    mockedApi.confirmToolExecution.mockResolvedValue(undefined as never);
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        captured = cbs as api.StreamCallbacks;
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true,
        value: vi.fn(),
    });
    useNotificationStore.setState({
        notifications: [],
        showPanel: false,
        typeFilter: null,
        alwaysAllowGrants: [],
    });
});

async function openChat(chat: Session) {
    useChatStore.setState({
        agents: [DOC_AGENT],
        activeAgentId: 'doc',
        sessions: [CHAT_A, CHAT_B],
        currentSessionId: chat.id,
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
    const view = render(<ChatView sessionId={chat.id} />);
    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message input'), { target: { value: 'list files' } });
        fireEvent.click(screen.getByLabelText('Send message'));
    });
    expect(captured).not.toBeNull();
    return view;
}

function emit(event: StreamEvent) {
    act(() => {
        captured!.onAgentEvent(event);
    });
}

const pendingPrompts = () =>
    useNotificationStore
        .getState()
        .notifications.filter((n) => n.type === 'permission_request' && !n.response);

describe('ChatView tool_confirm auto-approve', () => {
    it('a remember=false allow is never auto-approved later', async () => {
        await openChat(CHAT_A);

        emit(toolConfirm('c1'));
        expect(pendingPrompts().map((n) => n.id)).toEqual(['c1']);
        expect(pendingPrompts()[0].sessionId).toBe('chat-A');
        await act(() => useNotificationStore.getState().respondToPermission('c1', 'allow', false));

        emit(toolConfirm('c2'));
        expect(mockedApi.confirmToolExecution).not.toHaveBeenCalled();
        expect(pendingPrompts().map((n) => n.id)).toEqual(['c2']);
    });

    it('an "allow for the rest of this chat" grant auto-approves the next call in that chat', async () => {
        await openChat(CHAT_A);

        emit(toolConfirm('c1'));
        await act(() => useNotificationStore.getState().respondToPermission('c1', 'allow', true));

        emit(toolConfirm('c2'));
        expect(mockedApi.confirmToolExecution).toHaveBeenCalledWith('chat-A', 'c2', 'allow', false);
        expect(pendingPrompts()).toEqual([]);
    });

    it('a grant from one chat still prompts in another chat', async () => {
        useNotificationStore.setState({
            alwaysAllowGrants: [{ sessionId: 'chat-A', tool: 'run_shell_command', grantedAt: 1 }],
        });
        await openChat(CHAT_B);

        emit(toolConfirm('c9'));
        expect(mockedApi.confirmToolExecution).not.toHaveBeenCalled();
        expect(pendingPrompts().map((n) => n.id)).toEqual(['c9']);
    });

    it('a revoked grant prompts again', async () => {
        useNotificationStore.setState({
            alwaysAllowGrants: [{ sessionId: 'chat-A', tool: 'run_shell_command', grantedAt: 1 }],
        });
        await openChat(CHAT_A);
        useNotificationStore.getState().revokeAlwaysAllow('chat-A', 'run_shell_command');

        emit(toolConfirm('c3'));
        expect(mockedApi.confirmToolExecution).not.toHaveBeenCalled();
        expect(pendingPrompts().map((n) => n.id)).toEqual(['c3']);
    });
});
