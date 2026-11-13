// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A document the agent indexes on request joins the library and this chat:
// the turn's `done` event carries it, and the document bar shows it at once.

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Document, Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1',
    title: 'Plant questions',
    created_at: '2026-11-04T00:00:00Z',
    updated_at: '2026-11-04T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'gaia',
};

const MANUAL: Document = {
    id: 'doc-1',
    filename: 'plant_manual.md',
    filepath: 'C:\\Users\\me\\plant_manual.md',
    file_size: 120,
    chunk_count: 3,
    indexed_at: '2026-11-04T00:00:00Z',
    last_accessed_at: null,
    sessions_using: 1,
    indexing_status: 'complete',
};

let callbacks: api.StreamCallbacks | null = null;

beforeEach(() => {
    vi.clearAllMocks();
    callbacks = null;
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.listTurnChanges.mockResolvedValue({ turns: [] });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [],
        total: 0,
        total_size_bytes: 0,
        total_chunks: 0,
    });
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        callbacks = cbs as api.StreamCallbacks;
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
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

async function answerWith(done: Partial<StreamEvent>) {
    render(<ChatView sessionId={SESSION.id} />);
    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), {
            target: { value: 'Add plant_manual.md to my documents' },
        });
        fireEvent.click(screen.getByLabelText('Send'));
    });
    await act(async () => {
        callbacks!.onDone({ type: 'done', message_id: 7, content: 'Added it.', ...done } as StreamEvent);
    });
}

it('shows a document the agent indexed in the chat and the library', async () => {
    await answerWith({ documents_added: [MANUAL] });

    const state = useChatStore.getState();
    expect(state.documents.map((d) => d.id)).toEqual(['doc-1']);
    expect(state.sessions[0].document_ids).toEqual(['doc-1']);
    const bar = screen.getByLabelText('1 indexed document');
    expect(within(bar).getByText('plant_manual.md')).toBeInTheDocument();
});

it('does not list a document the chat already has twice', async () => {
    useChatStore.setState({
        sessions: [{ ...SESSION, document_ids: ['doc-1'] }],
        documents: [{ ...MANUAL, chunk_count: 0 }],
    });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [{ ...MANUAL, chunk_count: 0 }],
        total: 1,
        total_size_bytes: 120,
        total_chunks: 0,
    });

    await answerWith({ documents_added: [MANUAL] });

    const state = useChatStore.getState();
    expect(state.sessions[0].document_ids).toEqual(['doc-1']);
    expect(state.documents).toEqual([MANUAL]);
});

it('keeps library notices on the finished turn', async () => {
    const notice = "run.exe couldn't be added to your documents (.exe files are not supported).";
    mockedApi.getMessages
        .mockResolvedValueOnce({ messages: [], total: 0 })
        .mockResolvedValue({
            messages: [{
                id: 7,
                session_id: SESSION.id,
                role: 'assistant',
                content: 'Added it.',
                created_at: '2026-11-04T00:00:01Z',
                rag_sources: null,
                libraryNotices: [notice],
            }],
            total: 1,
        });
    await answerWith({ library_notices: [notice] });

    expect(await screen.findByRole('note')).toHaveTextContent(notice);
    // English notices keep their own direction inside an RTL chat.
    expect(screen.getByText(notice)).toHaveAttribute('dir', 'auto');
    expect(useChatStore.getState().messages.slice(-1)[0]?.libraryNotices).toEqual([notice]);
});

it('leaves the chat alone when the turn indexed nothing', async () => {
    await answerWith({});

    const state = useChatStore.getState();
    expect(state.documents).toEqual([]);
    expect(state.sessions[0].document_ids).toEqual([]);
});
