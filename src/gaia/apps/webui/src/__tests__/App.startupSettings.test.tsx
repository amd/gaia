// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The desktop window loads before the backend listens. Settings read at mount
 * must keep trying until the backend answers, and a missing GAIA agent must be
 * said plainly, with a way to install it.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { resetModelRetryForTests, useModelStore } from '../stores/modelStore';
import type { AgentInfo, Session, SetupCheck } from '../types';

const mocked = vi.mocked(api);

const READY: SetupCheck = { ready: true, steps: [] };
const GAIA_AGENT: AgentInfo = {
    id: 'gaia', name: 'GAIA', description: '', source: 'installed', conversation_starters: [], models: [],
};
const NOT_LISTENING = () => new TypeError('Failed to fetch');

function session(over: Partial<Session> = {}): Session {
    return {
        id: 'sess-new-0001',
        title: 'New Task',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        model: '',
        system_prompt: null,
        message_count: 0,
        document_ids: [],
        agent_type: 'gaia',
        ...over,
    };
}

beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    vi.stubGlobal('EventSource', class {
        close() { /* noop */ }
        addEventListener() { /* noop */ }
    });

    mocked.getActiveModel.mockResolvedValue({
        provider: 'local', model: 'Qwen3.6-35B-A3B-GGUF', label: 'Qwen', display_name: 'Qwen 3.6', remote: false, is_default: true,
    } as never);
    mocked.listProviderModels.mockResolvedValue({ models: [] } as never);
    mocked.checkSetup.mockResolvedValue(READY);
    mocked.requestWarmUp.mockResolvedValue({ state: 'ready' });
    mocked.getStartScreen.mockResolvedValue({ cards: [], working_folder: { path: '/w', name: 'w', gaia_folder: false } });
    mocked.getSystemStatus.mockResolvedValue({ lemonade_running: true, model_loaded: 'x', initialized: true } as never);
    mocked.listAgents.mockResolvedValue({ agents: [GAIA_AGENT], total: 1 });
    mocked.listSessions.mockResolvedValue({ sessions: [], total: 0 });
    mocked.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mocked.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mocked.getTunnelStatus.mockResolvedValue({ active: false } as never);
    mocked.createSession.mockResolvedValue(session());
    mocked.getMessages.mockResolvedValue({ messages: [] } as never);
    mocked.getPermissions.mockResolvedValue({ session_id: 'sess-new-0001', mode: 'full_access', grants: [] });
    mocked.setPermissionMode.mockImplementation(async (sid, mode) => ({ session_id: sid, mode, grants: [] }));
    mocked.listAllPermissions.mockResolvedValue({ default_mode: 'full_access', sessions: [] });
    mocked.sendMessageStream.mockImplementation(() => new AbortController());
    mocked.listDocuments.mockResolvedValue({ documents: [] } as never);
    mocked.getMessageCount.mockResolvedValue({ count: 0 } as never);

    resetModelRetryForTests();
    useModelStore.setState({ active: null, error: null, restoreNotice: null, names: {} });
    useChatStore.setState({
        sessions: [], currentSessionId: null, messages: [], agents: [], pendingPrompt: null,
        draftPermissionMode: 'ask', draftPermissionModeChosen: false,
        defaultPermissionMode: 'ask', defaultPermissionModeKnown: false,
        showMemoryDashboard: false, showSchedules: false, settingsSection: null, draftDocumentIds: [],
    });
});

afterEach(() => {
    resetModelRetryForTests();
    vi.useRealTimers();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
});

async function advance(ms: number) {
    await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

async function send(text: string) {
    const box = screen.getByLabelText('Message');
    fireEvent.change(box, { target: { value: text } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Send' })); });
    await advance(0);
}

describe('settings read before the backend listens', () => {
    it('the model chip names the model once the backend answers', async () => {
        mocked.getActiveModel.mockRejectedValueOnce(NOT_LISTENING()).mockRejectedValueOnce(NOT_LISTENING());
        render(<App />);
        await advance(0);
        // The composer's right-hand chip is the model's.
        const chipLabel = () => [...document.querySelectorAll('.composer-chip-label')].pop()?.textContent ?? '';
        expect(chipLabel()).toMatch(/^Model/);

        await advance(3_100);
        expect(mocked.getActiveModel).toHaveBeenCalledTimes(3);
        expect(chipLabel()).toMatch(/^Qwen 3\.6/);
    });

    it('a new chat starts in the saved default (full access) when the first read failed', async () => {
        mocked.listAllPermissions.mockRejectedValueOnce(NOT_LISTENING());
        render(<App />);
        await advance(0);
        expect(screen.getByRole('button', { name: 'Permission mode: ask before acting' })).toBeInTheDocument();

        await advance(1_100);
        expect(screen.getByRole('button', { name: 'Permission mode: full access' })).toBeInTheDocument();

        await send('go');
        expect(mocked.setPermissionMode).toHaveBeenCalledWith('sess-new-0001', 'full_access');
    });

    it('reads the saved default before applying a guess, when sent before the retry', async () => {
        mocked.listAllPermissions.mockRejectedValueOnce(NOT_LISTENING());
        render(<App />);
        await advance(0);
        await send('go');
        expect(mocked.setPermissionMode).toHaveBeenCalledWith('sess-new-0001', 'full_access');
        expect(mocked.setPermissionMode).not.toHaveBeenCalledWith('sess-new-0001', 'ask');
    });

    it('keeps a mode the user picked, even when the default arrives later', async () => {
        mocked.listAllPermissions.mockRejectedValueOnce(NOT_LISTENING());
        render(<App />);
        await advance(0);
        act(() => { useChatStore.getState().setDraftPermissionMode('ask'); });

        await advance(1_100);
        expect(useChatStore.getState().defaultPermissionMode).toBe('full_access');
        await send('go');
        expect(mocked.setPermissionMode).toHaveBeenCalledWith('sess-new-0001', 'ask');
    });

    it('re-reads at once when the status poll sees the backend come back', async () => {
        // Reads fail at 0, 1 and 3 s, so the next retry is due at 7 s; the status poll is down
        // at 0 s and answers at 5 s.
        mocked.getSystemStatus.mockRejectedValueOnce(NOT_LISTENING());
        mocked.listAllPermissions
            .mockRejectedValueOnce(NOT_LISTENING())
            .mockRejectedValueOnce(NOT_LISTENING())
            .mockRejectedValueOnce(NOT_LISTENING());
        render(<App />);
        await advance(3_100);
        expect(mocked.listAllPermissions).toHaveBeenCalledTimes(3);

        await advance(2_000);
        expect(mocked.getSystemStatus).toHaveBeenCalledTimes(2);
        expect(mocked.listAllPermissions).toHaveBeenCalledTimes(4);
        expect(useChatStore.getState().draftPermissionMode).toBe('full_access');
    });
});

describe('the GAIA agent is not installed', () => {
    it('says so and installs it from the notice', async () => {
        mocked.listAgents.mockResolvedValueOnce({ agents: [], total: 0 });
        mocked.installAgent.mockResolvedValue({ id: 'gaia', status: 'queued' });
        mocked.getAgentInstallStatus
            .mockResolvedValueOnce({ id: 'gaia', status: 'running', phase: 'download', percent: 40, version: null, error: null })
            .mockResolvedValueOnce({ id: 'gaia', status: 'completed', phase: 'done', percent: 100, version: '1.0.0', error: null });
        render(<App />);
        await advance(0);
        expect(screen.getByText(/GAIA agent isn't installed/)).toBeInTheDocument();

        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Install GAIA agent' })); });
        await advance(0);
        expect(mocked.installAgent).toHaveBeenCalledWith('gaia');
        expect(screen.getByText(/Installing the GAIA agent… 40%/)).toBeInTheDocument();

        await advance(2_000);
        expect(mocked.listAgents).toHaveBeenCalledTimes(2);
        expect(screen.queryByText(/GAIA agent isn't installed/)).not.toBeInTheDocument();
        expect(screen.queryByText(/Installing the GAIA agent/)).not.toBeInTheDocument();
    });

    it('shows the backend reason when the install fails, and offers to try again', async () => {
        mocked.listAgents.mockResolvedValue({ agents: [], total: 0 });
        mocked.installAgent.mockResolvedValue({ id: 'gaia', status: 'queued' });
        mocked.getAgentInstallStatus.mockResolvedValue({
            id: 'gaia', status: 'failed', phase: 'failed', percent: 0, version: null, error: 'Agent Hub unreachable',
        });
        render(<App />);
        await advance(0);
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Install GAIA agent' })); });
        await advance(0);
        expect(screen.getByText(/could not be installed: Agent Hub unreachable/)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    });

    it('a chat refused for the missing agent says why instead of the raw 422', async () => {
        // The agent list never loaded, so only the refusal can tell.
        mocked.listAgents.mockRejectedValue(NOT_LISTENING());
        mocked.createSession.mockRejectedValue(Object.assign(new Error("Unknown agent_type 'gaia'. Registered agent ids: chat."), {
            httpStatus: 422, detail: "Unknown agent_type 'gaia'. Registered agent ids: chat.",
        }));
        render(<App />);
        await advance(0);
        await send('hello');

        expect(screen.getByRole('alert')).toHaveTextContent(/GAIA agent isn't installed/);
        expect(screen.getByRole('button', { name: 'Install GAIA agent' })).toBeInTheDocument();
        expect(mocked.sendMessageStream).not.toHaveBeenCalled();
    });
});
