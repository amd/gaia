// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A stopped answer offers Continue and Try again (#7363). Continue asks GAIA
 * to carry on from the partial answer; Try again asks the question afresh
 * through Resend's path.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Session, SystemStatus } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1', title: 'Canals', created_at: '2026-06-10T00:00:00Z', updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen', system_prompt: null, message_count: 2, document_ids: [], agent_type: 'gaia',
};
const READY = { lemonade_running: true, initialized: true, init_state: 'ready', init_tasks: [] } as unknown as SystemStatus;
const QUESTION = 'Write about the history of canals.';

type Row = { id: number; session_id: string; role: 'user' | 'assistant'; content: string; created_at: string; rag_sources: null; stopped?: boolean };
const row = (id: number, role: 'user' | 'assistant', content: string, stopped = false): Row => ({
    id, session_id: SESSION.id, role, content, created_at: '2026-06-10T00:00:00Z', rag_sources: null, ...(stopped ? { stopped } : {}),
});

let server: Row[] = [];
let sends: string[] = [];

function start(reply: string) {
    server = [row(1, 'user', QUESTION), row(2, 'assistant', reply, true)];
    useChatStore.setState({
        sessions: [SESSION], currentSessionId: SESSION.id, messages: [], documents: [],
        isStreaming: false, streamingContent: '', agentSteps: [], cards: [], isLoadingMessages: false,
        pendingPrompt: null, systemStatus: READY, messageQueues: {}, pendingDeleteIds: [],
    });
    render(<ChatView sessionId={SESSION.id} />);
}

beforeEach(() => {
    vi.clearAllMocks();
    sends = [];
    mockedApi.getMessages.mockImplementation(async () => ({ messages: [...server], total: server.length }) as never);
    mockedApi.getMessageCount.mockImplementation(async () => server.length);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.getSystemStatus.mockResolvedValue(READY);
    mockedApi.deleteMessagesFrom.mockImplementation(async (_sid, id) => {
        const at = server.findIndex((m) => m.id === id);
        const count = server.length - at;
        server = server.slice(0, at);
        return { deleted: true, count };
    });
    mockedApi.sendMessageStream.mockImplementation((_sid, text) => {
        sends.push(text as string);
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
});

describe('a stopped answer', () => {
    it('continues from the partial answer', async () => {
        start('The earliest canals carried water, not boats.');
        const button = await screen.findByRole('button', { name: 'Continue' });
        expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
        await act(async () => {
            fireEvent.click(button);
        });

        await waitFor(() => expect(sends).toEqual(['Continue your answer from where it stopped.']));
        expect(mockedApi.deleteMessagesFrom).not.toHaveBeenCalled();
    });

    it('asks the question again on Try again', async () => {
        start('The earliest canals carried water, not boats.');
        const button = await screen.findByRole('button', { name: 'Try again' });
        await act(async () => {
            fireEvent.click(button);
        });

        await waitFor(() => expect(sends).toEqual([QUESTION]));
        expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledWith(SESSION.id, 1);
    });

    it('offers only Try again when it was stopped before writing anything', async () => {
        start('Cancelled.');
        await screen.findByRole('button', { name: 'Try again' });
        expect(screen.queryByRole('button', { name: 'Continue' })).toBeNull();
    });
});
