// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The streaming bubble's live line names the model's phase, like the TUI's. */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
import type { AgentInfo, Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

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

beforeEach(() => {
    vi.clearAllMocks();
    capturedCallbacks = null;

    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
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
    render(<ChatView sessionId={SESSION.id} />);

    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), {
            target: { value: 'send the drafted email' },
        });
        fireEvent.click(screen.getByLabelText('Send'));
    });

    expect(capturedCallbacks).not.toBeNull();
}

/** The live line beside GAIA's name; the activity panel may repeat the same text. */
async function liveLine(text: string) {
    await waitFor(() => expect(document.querySelector('.thinking-indicator-text')?.textContent).toBe(text));
}

describe('ChatView live status', () => {
    it('says Thinking... until a phase event arrives, then shows the latest phase', async () => {
        await driveSend();
        await liveLine('Thinking...');

        act(() => {
            capturedCallbacks!.onAgentEvent({
                type: 'status', status: 'working', phase: 'reading', message: 'Reading your request',
            } as unknown as StreamEvent);
        });
        await liveLine('Reading your request');

        act(() => {
            capturedCallbacks!.onAgentEvent({
                type: 'status', status: 'working', phase: 'reasoning', message: 'Reasoning — 38 words so far', words: 38,
            } as unknown as StreamEvent);
        });
        await liveLine('Reasoning — 38 words so far');
        expect(useChatStore.getState().liveStatus).toBe('Reasoning — 38 words so far');
    });

    it('ignores status messages that are not phases', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onAgentEvent({
                type: 'status', status: 'warning', message: 'Full access is ON',
            } as unknown as StreamEvent);
        });
        expect(useChatStore.getState().liveStatus).toBeNull();
    });

    it('drops the phase once a tool starts', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onAgentEvent({
                type: 'status', status: 'working', phase: 'tool_call', message: 'Preparing write_file',
            } as unknown as StreamEvent);
            capturedCallbacks!.onAgentEvent({ type: 'tool_start', tool: 'write_file' } as unknown as StreamEvent);
        });
        expect(useChatStore.getState().liveStatus).toBeNull();
    });
});
