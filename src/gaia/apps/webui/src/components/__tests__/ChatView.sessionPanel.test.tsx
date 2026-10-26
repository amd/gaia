// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The session panel's "This answer" shows the turn summary, live and after a reload (#5222, #5279). */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { useSessionPanelStore } from '../../stores/sessionPanelStore';
import type { Session, StreamEvent, TurnSummary } from '../../types';
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

const SUMMARY: TurnSummary = {
    pages: [{ url: 'https://www.amd.com/en', title: 'AMD', kind: 'page' }],
    documents: [],
    files: [],
    sites: ['amd.com'],
    live_data: { attempted: 1, failed: 0 },
    cost: { kind: 'estimate', usd: 0.002 },
    model: { remote: true, provider: 'amd', provider_name: 'AMD LLM Gateway', name: 'm' },
};

let callbacks: api.StreamCallbacks | null = null;

beforeEach(() => {
    vi.clearAllMocks();
    callbacks = null;
    // Any API call the view adds later stays pending instead of returning undefined.
    for (const fn of Object.values(mockedApi)) {
        if (vi.isMockFunction(fn)) fn.mockReturnValue(new Promise(() => {}) as never);
    }
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} } as never);
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        callbacks = cbs as api.StreamCallbacks;
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    window.localStorage.clear();
    useSessionPanelStore.getState().reload();
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
    });
});

function panel() {
    return screen.getByRole('complementary', { name: 'Session panel' });
}

describe('ChatView session panel: this answer', () => {
    it('appears when the turn ends with a summary', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        expect(screen.queryByRole('button', { name: 'This answer' })).not.toBeInTheDocument();
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'what is new at amd' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        await act(async () => {
            callbacks!.onDone({ type: 'done', content: 'answer', message_id: 7, turn_summary: SUMMARY } as StreamEvent);
        });
        expect(screen.getByRole('button', { name: 'This answer' })).toHaveAttribute('aria-expanded', 'true');
        expect(panel()).toHaveTextContent('AMD LLM Gateway (cloud)');
        expect(screen.getByRole('link', { name: /AMD/ })).toHaveAttribute('href', 'https://www.amd.com/en');
    });

    it("shows the turn in progress, not the previous answer's data", async () => {
        const finished = {
            messages: [
                { id: 1, session_id: SESSION.id, role: 'user', content: 'first', created_at: '', rag_sources: null },
                { id: 2, session_id: SESSION.id, role: 'assistant', content: 'a', created_at: '', rag_sources: null, turn_summary: SUMMARY },
            ],
            total: 2,
        };
        mockedApi.getMessages.mockResolvedValue(finished as never);
        render(<ChatView sessionId={SESSION.id} />);
        await waitFor(() => expect(
            panel(),
        ).toHaveTextContent('AMD LLM Gateway (cloud)'));
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'second' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        await waitFor(() => expect(
            panel(),
        ).toHaveTextContent('Working on this answer'));
        expect(panel()).not.toHaveTextContent('AMD LLM Gateway');
    });

    it('rebuilds the section from a reloaded message', async () => {
        mockedApi.getMessages.mockResolvedValue({
            messages: [
                { id: 1, session_id: SESSION.id, role: 'user', content: 'q', created_at: '', rag_sources: null },
                { id: 2, session_id: SESSION.id, role: 'assistant', content: 'a', created_at: '', rag_sources: null, turn_summary: SUMMARY },
            ],
            total: 2,
        } as never);
        render(<ChatView sessionId={SESSION.id} />);
        await waitFor(() => expect(
            panel(),
        ).toHaveTextContent('amd.com'));
    });
});
