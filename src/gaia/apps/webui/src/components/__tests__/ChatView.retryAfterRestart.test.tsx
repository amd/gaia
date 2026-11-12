// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * GAIA's backend crashed mid-answer and the desktop app restarted it. The
 * chat shows the cut-off answer with Try again. The window's last status may
 * still say "starting" (it was read while the new backend booted), and a send
 * is refused while GAIA starts. Try again must then re-ask the question, or
 * leave the question and the partial answer where they are; it must never
 * delete them and send nothing.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
import type { Message, Session, SystemStatus } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1',
    title: 'History of canals',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 2,
    document_ids: [],
    agent_type: 'gaia',
};

const QUESTION = 'Write about 1200 words on the history of canals.';
const CUT_OFF = 'The earliest canals were not built for boats but for water.\n\n'
    + 'This answer was interrupted when GAIA closed. Send again to retry.';

const STARTING = {
    lemonade_running: true, initialized: false, init_state: 'initializing',
    init_tasks: [{ name: 'Loading AI model', status: 'running' }],
} as unknown as SystemStatus;
const READY = { lemonade_running: true, initialized: true, init_state: 'ready', init_tasks: [] } as unknown as SystemStatus;

const row = (id: number, role: 'user' | 'assistant', content: string): Message => ({
    id, session_id: SESSION.id, role, content, created_at: '2026-06-10T00:00:00Z', rag_sources: null,
});

let server: Message[] = [];
let sends: string[] = [];

beforeEach(() => {
    vi.clearAllMocks();
    server = [row(322, 'user', QUESTION), row(323, 'assistant', CUT_OFF)];
    sends = [];
    mockedApi.getMessages.mockImplementation(async () => ({ messages: [...server], total: server.length }));
    mockedApi.getMessageCount.mockImplementation(async () => server.length);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.deleteMessagesFrom.mockImplementation(async (_sid, id) => {
        const at = server.findIndex((m) => m.id === id);
        if (at < 0) throw new Error('Message not found');
        const count = server.length - at;
        server = server.slice(0, at);
        return { deleted: true, count };
    });
    mockedApi.sendMessageStream.mockImplementation((_sid, text) => {
        sends.push(text as string);
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useNotificationStore.setState({ notifications: [] });
    useChatStore.setState({
        sessions: [SESSION], currentSessionId: SESSION.id, messages: [...server], documents: [],
        isStreaming: false, streamingContent: '', agentSteps: [], cards: [], isLoadingMessages: false,
        pendingPrompt: null, systemStatus: STARTING, messageQueues: {}, pendingDeleteIds: [],
    });
});

async function tryAgain() {
    render(<ChatView sessionId={SESSION.id} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy());
    await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    });
}

describe('Try again after the backend restarted', () => {
    it('re-asks the question once the backend says it has started', async () => {
        mockedApi.getSystemStatus.mockResolvedValue(READY);
        await tryAgain();

        await waitFor(() => expect(sends).toEqual([QUESTION]));
        expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledWith(SESSION.id, 322);
        expect(useChatStore.getState().systemStatus).toEqual(READY);
    });

    it('deletes nothing while GAIA is still starting, and says so', async () => {
        mockedApi.getSystemStatus.mockResolvedValue(STARTING);
        await tryAgain();

        await waitFor(() => expect(useNotificationStore.getState().notifications).toHaveLength(1));
        expect(mockedApi.deleteMessagesFrom).not.toHaveBeenCalled();
        expect(sends).toEqual([]);
        expect(server.map((m) => m.id)).toEqual([322, 323]);
        expect(useChatStore.getState().messages.map((m) => m.content)).toEqual([QUESTION, CUT_OFF]);
        expect(useNotificationStore.getState().notifications[0].message).toMatch(/still starting/);
    });

    it('deletes nothing when the status cannot be read', async () => {
        mockedApi.getSystemStatus.mockRejectedValue(new TypeError('Failed to fetch'));
        await tryAgain();

        await waitFor(() => expect(useNotificationStore.getState().notifications).toHaveLength(1));
        expect(mockedApi.deleteMessagesFrom).not.toHaveBeenCalled();
        expect(sends).toEqual([]);
        expect(useChatStore.getState().messages.map((m) => m.content)).toEqual([QUESTION, CUT_OFF]);
    });
});
