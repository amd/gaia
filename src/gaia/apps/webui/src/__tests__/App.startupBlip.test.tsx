// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A PC set up before shows the chat UI while the setup check runs. A status
 * poll that briefly loses the backend must not swap that UI for "Starting
 * GAIA…", or a click on it (Settings) lands on a screen about to vanish.
 */

import { act, render, screen } from '@testing-library/react';
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

const NEEDED: SetupCheck = { ready: false, steps: [] };

let answerSetup: (check: SetupCheck) => void = () => {};

function statusSays(initialized: boolean) {
    mocked.getSystemStatus.mockResolvedValue({
        lemonade_running: true, model_loaded: 'x', initialized,
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
    // The setup check stays pending until a test answers it.
    mocked.checkSetup.mockImplementation(() => new Promise<SetupCheck>((resolve) => { answerSetup = resolve; }));
    statusSays(true);
    mocked.listAgents.mockResolvedValue({ agents: [], total: 0 });
    mocked.listSessions.mockResolvedValue({ sessions: [], total: 0 });
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

describe('startup screen during the setup check', () => {
    it('keeps the chat UI and its Settings button through a status blip', async () => {
        render(<App />);
        await advance(0);
        expect(text()).not.toContain('Starting GAIA');
        const settings = screen.getByRole('button', { name: /settings/i });

        act(() => { useChatStore.getState().setSystemStatus(null); });

        expect(text()).not.toContain('Starting GAIA');
        expect(settings).toBeInTheDocument();
    });

    it('a new install still waits on the startup screen', async () => {
        statusSays(false);
        render(<App />);
        await advance(0);
        expect(text()).toContain('Starting GAIA');
    });

    it('a blip before the chat UI was ever shown keeps the startup screen', async () => {
        statusSays(false);
        render(<App />);
        await advance(0);
        act(() => { useChatStore.getState().setSystemStatus(null); });
        expect(text()).toContain('Starting GAIA');
    });

    it('still shows first-run setup when the check finds it needed after a blip', async () => {
        render(<App />);
        await advance(0);
        act(() => { useChatStore.getState().setSystemStatus(null); });
        await act(async () => { answerSetup(NEEDED); });
        expect(text()).toContain('First-run setup');
    });
});
