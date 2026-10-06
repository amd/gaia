// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Chat view state that went stale after a mutation. */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Document, Message, Session } from '../../types';
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

const DOC: Document = {
    id: 'doc-1',
    filename: 'report.pdf',
    filepath: 'C:/Users/me/report.pdf',
    file_size: 1024,
    chunk_count: 3,
    indexed_at: '2026-06-10T00:00:00Z',
    last_accessed_at: null,
    sessions_using: 1,
    indexing_status: 'complete',
} as Document;

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [], total: 0, total_size_bytes: 0, total_chunks: 0,
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        agents: [], sessions: [SESSION], currentSessionId: SESSION.id,
        messages: [], documents: [], isStreaming: false, streamingContent: '',
        agentSteps: [], cards: [], isLoadingMessages: false, pendingPrompt: null,
        systemStatus: null,
    });
});

describe('a file dropped onto the chat', () => {
    it('shows in the document bar without reopening the chat', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        await waitFor(() => expect(mockedApi.listDocuments).toHaveBeenCalled());
        mockedApi.uploadDocumentBlob.mockResolvedValue(DOC);
        mockedApi.attachDocument.mockResolvedValue(undefined as never);
        mockedApi.listDocuments.mockResolvedValue({
            documents: [DOC], total: 1, total_size_bytes: 1024, total_chunks: 3,
        });

        const file = new File(['x'], 'report.pdf', { type: 'application/pdf' });
        await act(async () => {
            fireEvent.drop(screen.getByRole('main'), { dataTransfer: { files: [file] } });
        });

        expect(await screen.findByText('report.pdf')).toBeInTheDocument();
        expect(screen.getByText('1 indexed')).toBeInTheDocument();
    });
});

describe('a message delete the server rejects', () => {
    it('restores the messages with their steps and stats', async () => {
        const question = {
            id: 1, session_id: SESSION.id, role: 'user', content: 'Read it',
            created_at: '2026-06-10T00:00:01Z', rag_sources: null,
        };
        const reply = {
            id: 2, session_id: SESSION.id, role: 'assistant', content: 'Done.',
            created_at: '2026-06-10T00:00:02Z', rag_sources: null,
            agent_steps: [{ id: 1, type: 'tool', label: 'Using tool', tool: 'read_file', active: false, timestamp: 1 }],
            inference_stats: { tokens_per_second: 12 },
        };
        mockedApi.getMessages.mockResolvedValue({ messages: [question, reply] as never, total: 2 });
        mockedApi.deleteMessage.mockRejectedValue(new Error('API 500'));
        render(<ChatView sessionId={SESSION.id} />);
        await waitFor(() => expect(useChatStore.getState().messages).toHaveLength(2));

        fireEvent.click(screen.getAllByRole('button', { name: 'Delete message' })[1]);
        fireEvent.click(screen.getByRole('button', { name: 'Confirm delete message' }));

        await waitFor(() => expect(mockedApi.deleteMessage).toHaveBeenCalledWith(SESSION.id, 2));
        await waitFor(() => {
            const restored = useChatStore.getState().messages.find((m) => m.id === 2) as Message | undefined;
            expect(restored?.agentSteps).toHaveLength(1);
            expect(restored?.stats).toEqual({ tokens_per_second: 12 });
        });
    });
});
