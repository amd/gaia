// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A PC that finished setup and starts while its model server is not answering
 * keeps its chats and says the server is down. It is not a first run: the
 * setup wizard would ask it to download what it already has.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');
vi.mock('../components/SetupScreen', () => ({
    SetupScreen: () => <p>First-run setup</p>,
}));

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore } from '../stores/modelStore';
import type { SetupCheck } from '../types';

const mocked = vi.mocked(api);

const SERVER_DOWN: SetupCheck = {
    ready: false, stage: 'server', initialized: true, steps: [],
    reasons: ['Lemonade Server at http://127.0.0.1:9/api/v1 is not reachable'],
};
const READY: SetupCheck = { ready: true, initialized: true, steps: [] };

function statusSays(lemonadeRunning: boolean) {
    mocked.getSystemStatus.mockResolvedValue({
        lemonade_running: lemonadeRunning, model_loaded: lemonadeRunning ? 'x' : null, initialized: true,
    } as never);
}

beforeEach(() => {
    vi.stubGlobal('EventSource', class {
        close() { /* noop */ }
        addEventListener() { /* noop */ }
    });
    mocked.getActiveModel.mockResolvedValue({
        provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true,
    } as never);
    mocked.checkSetup.mockResolvedValue(SERVER_DOWN);
    statusSays(false);
    mocked.listAgents.mockResolvedValue({ agents: [], total: 0 });
    mocked.listSessions.mockResolvedValue({
        sessions: [{ id: 's1', title: 'Harbor plans', updated_at: '2026-11-01T00:00:00Z' }], total: 1,
    } as never);
    mocked.listAllPermissions.mockResolvedValue({ default_mode: 'ask', sessions: [] });
    mocked.getStartScreen.mockResolvedValue({ cards: [], working_folder: { path: '/w', name: 'w', gaia_folder: false, choosable: true } });
    mocked.listDocuments.mockResolvedValue({ documents: [] } as never);
    mocked.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mocked.getTunnelStatus.mockResolvedValue({ active: false } as never);
    mocked.requestWarmUp.mockResolvedValue({ state: 'ready' });

    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    useChatStore.setState({
        sessions: [], currentSessionId: null, messages: [], agents: [], pendingPrompt: null, systemStatus: null,
        backendConnected: true, showMemoryDashboard: false, showSchedules: false, settingsSection: null,
        setupAgain: false,
    });
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
});

afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
});

async function advance(ms: number) {
    await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

const text = () => document.body.textContent ?? '';
const NOTICE = "GAIA's model server isn't answering, so it can't reply yet. Your chats are still here.";

describe('a set-up install whose model server is down at launch', () => {
    it('shows the chats and says the model server is not answering', async () => {
        render(<App />);
        await advance(0);

        expect(text()).not.toContain('First-run setup');
        expect(text()).toContain('Harbor plans');
        expect(text()).toContain(NOTICE);
    });

    it('Check again recovers in place once the server answers', async () => {
        render(<App />);
        await advance(0);
        mocked.checkSetup.mockResolvedValue(READY);
        statusSays(true);

        fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
        await advance(0);

        expect(text()).not.toContain(NOTICE);
        expect(text()).not.toContain('First-run setup');
        expect(text()).toContain('Harbor plans');
    });

    it('recovers on its own when the status poll sees the server again', async () => {
        render(<App />);
        await advance(0);
        const asked = mocked.checkSetup.mock.calls.length;
        mocked.checkSetup.mockResolvedValue(READY);

        act(() => {
            useChatStore.getState().setSystemStatus({ lemonade_running: true, model_loaded: 'x', initialized: true } as never);
        });
        await advance(0);

        expect(mocked.checkSetup.mock.calls.length).toBe(asked + 1);
        expect(text()).not.toContain(NOTICE);
    });

    it('does not re-check in a loop when the poll and the check disagree', async () => {
        render(<App />);
        await advance(0);
        const asked = mocked.checkSetup.mock.calls.length;

        act(() => {
            useChatStore.getState().setSystemStatus({ lemonade_running: true, model_loaded: 'x', initialized: true } as never);
        });
        await advance(0);
        await advance(0);

        expect(mocked.checkSetup.mock.calls.length).toBe(asked + 1);
        expect(text()).toContain(NOTICE);
        expect(text()).not.toContain('First-run setup');
    });

    it('offers setup from the notice, for a server that will not come back', async () => {
        render(<App />);
        await advance(0);

        fireEvent.click(screen.getByRole('button', { name: 'Open setup' }));
        await advance(0);

        expect(text()).toContain('First-run setup');
    });

    it('a PC never seen ready still gets first-run setup', async () => {
        mocked.checkSetup.mockResolvedValue({ ...SERVER_DOWN, initialized: false });
        render(<App />);
        await advance(0);

        expect(text()).toContain('First-run setup');
    });

    it('a model list in the wrong shape keeps the chats and says so, without offering setup (#8058)', async () => {
        mocked.checkSetup.mockResolvedValue({
            ...SERVER_DOWN, model_list_unread: true,
            reasons: ["Could not read the model list from http://127.0.0.1:9/api/v1: it isn't in the expected shape"],
        });
        render(<App />);
        await advance(0);

        expect(text()).not.toContain('First-run setup');
        expect(text()).toContain('Harbor plans');
        expect(text()).toContain("its list of models came back in a shape GAIA can't read");
        expect(text()).not.toContain(NOTICE);
        expect(screen.queryByRole('button', { name: 'Open setup' })).not.toBeInTheDocument();
    });

    it('a set-up PC missing something other than the server still gets setup', async () => {
        mocked.checkSetup.mockResolvedValue({ ready: false, stage: 'models', initialized: true, steps: [] });
        render(<App />);
        await advance(0);

        expect(text()).toContain('First-run setup');
    });
});
