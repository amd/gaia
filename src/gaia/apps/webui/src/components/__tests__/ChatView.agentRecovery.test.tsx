// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A turn in an open chat failed because the chosen gaiacpp can't start: the
 * chat offers the same two fixes a new chat does, under that turn, and once
 * one works it sends the question again the way Try again does.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { AgentPrograms, Message, Session, Settings, StartScreen, SystemStatus } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1',
    title: 'Trip plan',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 2,
    document_ids: [],
    agent_type: 'gaia',
};

const QUESTION = 'Plan three days in Lisbon.';
const READY = { lemonade_running: true, initialized: true, init_state: 'ready', init_tasks: [] } as unknown as SystemStatus;

const question: Message = {
    id: 41, session_id: SESSION.id, role: 'user', content: QUESTION, created_at: '2026-06-10T00:00:00Z', rag_sources: null,
};
const agentDown: Message = {
    id: 42, session_id: SESSION.id, role: 'assistant', content: '', created_at: '2026-06-10T00:00:01Z', rag_sources: null,
    failed: true,
    agentSteps: [{
        id: 1, type: 'error', label: 'Error', detail: "GAIA couldn't start its agent. Send your message again.",
        active: false, success: false, timestamp: 1,
    }],
};

const MISSING: StartScreen = {
    cards: [],
    working_folder: { path: '/home/you', name: 'you', gaia_folder: false, choosable: false },
    skills_error: 'Cannot start the GAIA agent',
    agent_problem: { code: 'gaiacpp_missing', detail: 'gaiacpp is chosen as the agent but it is not installed on this PC.' },
} as StartScreen;

const PROGRAMS: AgentPrograms = {
    current: 'gaiacpp',
    programs: [
        { id: 'gaiapy', installed: true, size_bytes: null, can_download: false, unavailable_reason: null },
        { id: 'gaiacpp', installed: false, size_bytes: 4_000_000, can_download: true, unavailable_reason: null },
    ],
    download: { state: 'idle', bytes_done: 0, bytes_total: null, error: null },
};

let server: Message[] = [];
let sends: string[] = [];

beforeEach(() => {
    vi.clearAllMocks();
    server = [question, agentDown];
    sends = [];
    mockedApi.getMessages.mockImplementation(async () => ({ messages: [...server], total: server.length }));
    mockedApi.getMessageCount.mockImplementation(async () => server.length);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.getSystemStatus.mockResolvedValue(READY);
    mockedApi.getStartScreen.mockResolvedValue(MISSING);
    mockedApi.getAgentPrograms.mockResolvedValue(PROGRAMS);
    mockedApi.updateSettings.mockResolvedValue({ flagship_agent: 'gaiapy' } as Settings);
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
    useChatStore.setState({
        sessions: [SESSION], currentSessionId: SESSION.id, messages: [...server], documents: [],
        isStreaming: false, streamingContent: '', agentSteps: [], cards: [], isLoadingMessages: false,
        pendingPrompt: null, systemStatus: READY, messageQueues: {}, pendingDeleteIds: [], locale: 'en',
    });
});

describe('an open chat whose agent could not start', () => {
    it('offers both fixes under the failed turn', async () => {
        render(<ChatView sessionId={SESSION.id} />);

        expect(await screen.findByText("gaiacpp, the agent you chose, isn't on this PC, so GAIA can't start it.")).toBeInTheDocument();
        expect(await screen.findByRole('button', { name: 'Download gaiacpp again' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Use Full-featured' })).toBeInTheDocument();
        const turn = document.querySelector('[data-message-id="42"]')!;
        expect(turn.querySelector('.agent-recovery')).not.toBeNull();
        expect(api.updateSettings).not.toHaveBeenCalled();
        expect(sends).toEqual([]);
    });

    it('offers nothing when the agent is not why the turn failed', async () => {
        mockedApi.getStartScreen.mockResolvedValue({ ...MISSING, skills_error: null, agent_problem: null });
        render(<ChatView sessionId={SESSION.id} />);

        await waitFor(() => expect(mockedApi.getStartScreen).toHaveBeenCalled());
        await act(async () => {});
        expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
        expect(document.querySelector('.agent-recovery')).toBeNull();
    });

    it('does not ask about the agent for a turn that did not fail', async () => {
        server = [question, { ...agentDown, failed: false, agentSteps: undefined, content: 'Day one: Alfama.' }];
        useChatStore.setState({ messages: [...server] });
        render(<ChatView sessionId={SESSION.id} />);

        await screen.findByText('Day one: Alfama.');
        expect(mockedApi.getStartScreen).not.toHaveBeenCalled();
        expect(document.querySelector('.agent-recovery')).toBeNull();
    });

    it('sends the question again the Try again way once Full-featured is chosen', async () => {
        render(<ChatView sessionId={SESSION.id} />);

        fireEvent.click(await screen.findByRole('button', { name: 'Use Full-featured' }));

        await waitFor(() => expect(api.updateSettings).toHaveBeenCalledWith({ flagship_agent: 'gaiapy' }));
        await waitFor(() => expect(sends).toEqual([QUESTION]));
        expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledWith(SESSION.id, 41);
    });

    it('sends the question again once gaiacpp is downloaded', async () => {
        mockedApi.startAgentDownload.mockResolvedValue({ state: 'done', bytes_done: 4_000_000, bytes_total: 4_000_000, error: null });
        render(<ChatView sessionId={SESSION.id} />);

        fireEvent.click(await screen.findByRole('button', { name: 'Download gaiacpp again' }));

        await waitFor(() => expect(sends).toEqual([QUESTION]));
        expect(api.updateSettings).not.toHaveBeenCalled();
        expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledWith(SESSION.id, 41);
    });

    it('keeps what the user is typing while it switches and sends again', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        const box = screen.getByLabelText('Message');
        fireEvent.change(box, { target: { value: 'and a day trip to Sintra' } });

        fireEvent.click(await screen.findByRole('button', { name: 'Use Full-featured' }));

        await waitFor(() => expect(sends).toEqual([QUESTION]));
        expect(screen.getByLabelText('Message')).toHaveValue('and a day trip to Sintra');
    });
});
