// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// newTask.test.ts pins what planNewTask decides. This file pins what the shell
// actually *sends*, which is a different claim: the planner could be perfect
// and App could still hand it the agent picker's id, which is how a new chat
// used to inherit the agent of whatever session you last opened.

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import type { Session } from '../types';

const mocked = vi.mocked(api);

function session(over: Partial<Session> = {}): Session {
    return {
        id: 'sess-legacy-0001',
        title: 'Old doc chat',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        model: '',
        system_prompt: null,
        message_count: 4,
        document_ids: [],
        ...over,
    };
}

beforeEach(() => {
    // App opens an SSE channel on mount; jsdom has no EventSource.
    vi.stubGlobal('EventSource', class {
        close() { /* noop */ }
        addEventListener() { /* noop */ }
    });

    mocked.getOnboardingStatus.mockResolvedValue({
        initialized: true,
        skipped: false,
        completed_at: '2026-01-01T00:00:00Z',
    });
    mocked.getSystemStatus.mockResolvedValue({
        lemonade_running: true,
        model_loaded: 'Gemma-4-E4B-it-GGUF',
    } as never);
    mocked.listAgents.mockResolvedValue({ agents: [], total: 0 });
    mocked.listSessions.mockResolvedValue({ sessions: [], total: 0 });
    mocked.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mocked.getTunnelStatus.mockResolvedValue({ active: false } as never);
    mocked.createSession.mockResolvedValue(session({ id: 'sess-new-0001', message_count: 0 }));
    mocked.getMessages.mockResolvedValue({ messages: [] } as never);

    useChatStore.setState({ sessions: [], currentSessionId: null, messages: [] });
});

afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
});

async function clickNewTask() {
    const btn = await screen.findByRole('button', { name: 'New Task' });
    await userEvent.click(btn);
}

describe('the New Task button', () => {
    it('creates the session with the flagship agent', async () => {
        render(<App />);
        await clickNewTask();

        await waitFor(() => expect(mocked.createSession).toHaveBeenCalled());
        expect(mocked.createSession.mock.calls[0][0]).toMatchObject({ agent_type: 'gaia' });
    });

    // The regression. `activeAgentId` is synced from the session you are
    // viewing, so before this change, opening an old doc chat and hitting
    // "New Task" silently produced another doc chat.
    it('still uses the flagship after viewing a legacy chat', async () => {
        const legacy = session({ agent_type: 'doc' });
        mocked.listSessions.mockResolvedValue({ sessions: [legacy], total: 1 });
        useChatStore.setState({
            sessions: [legacy],
            currentSessionId: legacy.id,
            activeAgentId: 'doc',
        });

        render(<App />);
        await clickNewTask();

        await waitFor(() => expect(mocked.createSession).toHaveBeenCalled());
        expect(mocked.createSession.mock.calls[0][0]).toMatchObject({ agent_type: 'gaia' });
        expect(mocked.createSession.mock.calls[0][0]).not.toMatchObject({ agent_type: 'doc' });
    });

    it('leaves the legacy conversation on its own agent', async () => {
        const legacy = session({ agent_type: 'doc' });
        mocked.listSessions.mockResolvedValue({ sessions: [legacy], total: 1 });
        useChatStore.setState({ sessions: [legacy], currentSessionId: legacy.id });

        render(<App />);
        await clickNewTask();

        await waitFor(() => expect(mocked.createSession).toHaveBeenCalled());
        // Creating a new chat must never rewrite an existing conversation.
        expect(mocked.updateSession).not.toHaveBeenCalled();
        expect(useChatStore.getState().sessions.find((s) => s.id === legacy.id)?.agent_type)
            .toBe('doc');
    });
});
