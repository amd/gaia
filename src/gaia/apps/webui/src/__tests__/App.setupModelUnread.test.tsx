// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Whether GAIA is set up depends on the model in use: a cloud model needs no
 * local download. The window asks the backend with that model's kind. When the
 * model can't be read but the setup check answers (the backend began listening
 * between the two, or the window's first requests went to a restarted
 * backend's old port), the answer is for a local model and says "not set up"
 * on a cloud install. That must never open first-run setup.
 */

import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore } from '../stores/modelStore';
import type { Session, SetupCheck } from '../types';

const mocked = vi.mocked(api);

const CHAT: Session = {
    id: 'sess-cloud-0001', title: 'History of canals', created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z', model: '', system_prompt: null, message_count: 2, document_ids: [],
};

const READY: SetupCheck = { ready: true, steps: [] };
const LOCAL_MISSING: SetupCheck = {
    ready: false, stage: 'setup', reasons: ['Model Gemma-4-E4B-it-GGUF is not downloaded'], steps: [],
};

const CLOUD_MODEL = {
    provider: 'fireworks', model: 'fireworks.deepseek-v4p1-flash', label: 'deepseek', remote: true, is_default: false,
};

let modelReads = 0;
let modelFailsFirst = 1;

beforeEach(() => {
    modelReads = 0;
    vi.stubGlobal('EventSource', class {
        close() { /* noop */ }
        addEventListener() { /* noop */ }
    });
    mocked.getActiveModel.mockImplementation(async () => {
        modelReads += 1;
        if (modelReads <= modelFailsFirst) throw new TypeError('Failed to fetch');
        return CLOUD_MODEL as never;
    });
    mocked.checkSetup.mockImplementation(async (remote?: boolean) => (remote ? READY : LOCAL_MISSING));
    mocked.getSystemStatus.mockResolvedValue({ lemonade_running: true, initialized: true } as never);
    mocked.listAgents.mockResolvedValue({ agents: [], total: 0 });
    mocked.listSessions.mockResolvedValue({ sessions: [CHAT], total: 1 });
    mocked.listAllPermissions.mockResolvedValue({ default_mode: 'ask' as const, sessions: [] });
    mocked.getStartScreen.mockResolvedValue({ cards: [], working_folder: { path: '/w', name: 'w', gaia_folder: false, choosable: true } });
    mocked.listDocuments.mockResolvedValue({ documents: [] } as never);
    mocked.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mocked.getTunnelStatus.mockResolvedValue({ active: false } as never);
    mocked.requestWarmUp.mockResolvedValue({ state: 'ready' as const });

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

describe('a cloud install whose model the window could not read yet', () => {
    it('opens the chats, not first-run setup', async () => {
        render(<App />);
        const seen: string[] = [];
        for (let t = 0; t < 5_000; t += 250) {
            await advance(250);
            seen.push(text());
        }
        for (const screen of seen) expect(screen).not.toContain('Set up GAIA');
        expect(text()).toContain('History of canals');
        // The answer it acted on was asked for the cloud model.
        expect(mocked.checkSetup).toHaveBeenLastCalledWith(true);
    });

    it('says it could not check, rather than guess, when the model never answers', async () => {
        modelFailsFirst = Number.POSITIVE_INFINITY;
        render(<App />);
        await advance(90_000);
        expect(text()).not.toContain('Set up GAIA');
        expect(text()).toMatch(/Could not check whether GAIA is set up/);
        modelFailsFirst = 1;
    });
});
