// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Sends that don't come from the composer (Resend, the connector Retry banner,
 * card buttons) send exactly their own text and leave the user's unsent draft
 * and its attachments in the box (#6950).
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Message, Session } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1',
    title: 'Rainier weather',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'gemma',
    system_prompt: null,
    message_count: 2,
    document_ids: [],
    agent_type: 'gaia',
};

const row = (id: number, role: 'user' | 'assistant', content: string): Message => ({
    id, session_id: SESSION.id, role, content, created_at: '2026-06-10T00:00:00Z', rag_sources: null,
});

const DRAFT = 'A follow-up I am still writing';
const UPLOADED = {
    filename: 'notes.pdf', original_name: 'notes.pdf', url: '/api/files/uploads/notes.pdf',
    size: 10, content_type: 'application/pdf', is_image: false,
};

let server: Message[] = [];
let sent: string[] = [];

beforeEach(() => {
    vi.clearAllMocks();
    server = [row(1, 'user', 'Check Rainier weather'), row(2, 'assistant', 'Clear Saturday.')];
    sent = [];
    mockedApi.getMessages.mockImplementation(async () => ({ messages: [...server], total: server.length }));
    mockedApi.getMessageCount.mockImplementation(async () => server.length);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: SESSION.id, mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.uploadFile.mockResolvedValue(UPLOADED as never);
    mockedApi.deleteMessagesFrom.mockImplementation(async (_sid, id) => {
        const at = server.findIndex((m) => m.id === id);
        const count = server.length - at;
        server = server.slice(0, at);
        return { deleted: true, count };
    });
    mockedApi.sendMessageStream.mockImplementation((_sid, text) => {
        sent.push(text as string);
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });

    useChatStore.setState({
        sessions: [SESSION],
        currentSessionId: SESSION.id,
        messages: [...server],
        documents: [],
        isStreaming: false,
        streamingContent: '',
        agentSteps: [],
        cards: [],
        isLoadingMessages: false,
        pendingPrompt: null,
        systemStatus: null,
        messageQueues: {},
        pendingDeleteIds: [],
    });
});

async function writeDraftWithFile() {
    render(<ChatView sessionId={SESSION.id} />);
    await screen.findByText('Clear Saturday.');
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: DRAFT } });
    const file = new File(['%PDF-1.4'], 'notes.pdf', { type: 'application/pdf' });
    await act(async () => {
        fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [file] } });
    });
    await waitFor(() => expect(mockedApi.uploadFile).toHaveBeenCalled());
    expect(screen.getByLabelText('Remove notes.pdf')).toBeInTheDocument();
}

function expectDraftKept() {
    expect(screen.getByLabelText('Message')).toHaveValue(DRAFT);
    expect(screen.getByLabelText('Remove notes.pdf')).toBeInTheDocument();
}

describe('ChatView programmatic sends (#6950)', () => {
    it('Resend sends only the earlier question and keeps the draft and its file', async () => {
        await writeDraftWithFile();

        await act(async () => {
            fireEvent.click(screen.getAllByLabelText('Resend message')[0]);
        });

        await waitFor(() => expect(sent).toEqual(['Check Rainier weather']));
        expectDraftKept();
    });

    it('a card button send keeps the draft and its file', async () => {
        await writeDraftWithFile();

        await act(async () => {
            window.dispatchEvent(new CustomEvent('gaia:send-message', { detail: { text: 'Approve' } }));
        });

        await waitFor(() => expect(sent).toEqual(['Approve']));
        expectDraftKept();
    });

    it('sending from the box still sends the draft with its file and empties the box', async () => {
        await writeDraftWithFile();

        await act(async () => {
            fireEvent.click(screen.getByLabelText('Send'));
        });

        await waitFor(() => expect(sent).toEqual([`${DRAFT}\n\n[notes.pdf](/api/files/uploads/notes.pdf)`]));
        expect(screen.getByLabelText('Message')).toHaveValue('');
        expect(screen.queryByLabelText('Remove notes.pdf')).toBeNull();
    });
});
