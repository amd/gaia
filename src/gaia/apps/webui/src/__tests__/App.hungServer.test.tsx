// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A model server that accepts connections but never answers holds the setup
 * check for a minute. The chat shows meanwhile, so the warning must too.
 */

import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore } from '../stores/modelStore';

const mocked = vi.mocked(api);
const never = () => new Promise<never>(() => {});

beforeEach(() => {
    vi.stubGlobal('EventSource', class {
        close() { /* noop */ }
        addEventListener() { /* noop */ }
    });
    mocked.getActiveModel.mockImplementation(never);
    mocked.checkSetup.mockImplementation(never);
    mocked.getSystemStatus.mockResolvedValue({
        lemonade_running: false, lemonade_probe: 'timed_out', lemonade_error: 'Lemonade did not answer in time',
        model_loaded: null, initialized: true, init_state: 'degraded',
    } as never);
    mocked.listAgents.mockResolvedValue({ agents: [], total: 0 });
    mocked.listSessions.mockResolvedValue({ sessions: [], total: 0 } as never);
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

describe('a set-up PC whose model server hangs', () => {
    it('warns that the model server is not answering while the setup check waits', async () => {
        render(<App />);
        await act(async () => { await vi.advanceTimersByTimeAsync(0); });

        expect(document.body.textContent).toContain("The model server isn't answering.");
    });

    it('keeps the same warning once the setup check gives up on the server', async () => {
        mocked.getActiveModel.mockResolvedValue({
            provider: 'fireworks', model: 'm', label: 'Cloud', remote: true, is_default: false,
        } as never);
        mocked.checkSetup.mockResolvedValue({
            ready: false, stage: 'server', initialized: true, steps: [], reasons: ['Lemonade did not answer in time'],
        } as never);
        render(<App />);
        await act(async () => { await vi.advanceTimersByTimeAsync(0); });

        const text = document.body.textContent ?? '';
        expect(text).toContain("The model server isn't answering.");
        expect(text).not.toContain("GAIA's model server isn't answering, so it can't reply yet.");
        expect(text).not.toContain('Set up local models');
    });
});
