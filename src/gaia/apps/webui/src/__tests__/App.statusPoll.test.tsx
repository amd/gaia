// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The status poll on a busy machine: one request in flight at a time, backing
 * off while answers are slow, and a model server that is only slow to answer
 * is never reported as stopped.
 */

import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore } from '../stores/modelStore';
import type { Session, SetupCheck, SystemStatus } from '../types';

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

const READY: SetupCheck = { ready: true, steps: [] };

beforeEach(() => {
    // App opens an SSE channel on mount; jsdom has no EventSource.
    vi.stubGlobal('EventSource', class {
        close() { /* noop */ }
        addEventListener() { /* noop */ }
    });

    mocked.getActiveModel.mockResolvedValue({
        provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true,
    });
    mocked.checkSetup.mockResolvedValue(READY);
    mocked.requestWarmUp.mockResolvedValue({ state: 'ready' });
    mocked.getSystemStatus.mockResolvedValue({
        lemonade_running: true,
        model_loaded: 'Gemma-4-E4B-it-GGUF',
    } as never);
    mocked.listAgents.mockResolvedValue({ agents: [], total: 0 });
    mocked.listSessions.mockResolvedValue({ sessions: [], total: 0 });
    mocked.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mocked.getTunnelStatus.mockResolvedValue({ active: false } as never);
    mocked.createSession.mockResolvedValue(session({ id: 'sess-new-0001', title: 'New Task', message_count: 0, agent_type: 'gaia' }));
    mocked.getMessages.mockResolvedValue({ messages: [] } as never);
    mocked.getPermissions.mockResolvedValue({ session_id: 'sess-new-0001', mode: 'ask', grants: [] });
    mocked.setPermissionMode.mockResolvedValue({ session_id: 'sess-new-0001', mode: 'full_access', grants: [] });
    mocked.listAllPermissions.mockResolvedValue({ default_mode: 'ask', sessions: [] });
    mocked.sendMessageStream.mockImplementation(() => new AbortController());
    mocked.getOnboardingPreflight.mockResolvedValue({
        compatible: true, blockers: [], warnings: [], ram_gb: 32, gpu_name: null, npu_detected: false, disk_free_gb: 100,
    } as never);
    mocked.getSetupStatus.mockResolvedValue({ state: 'idle' });
    mocked.getLemonadeTarget.mockResolvedValue({
        mode: 'gaia', saved_mode: null, source: 'gaia', base_url: null, saved_base_url: null,
        api_key_stored: false, gaia_installed: false, offer: false, what_gaia_does: '',
    });
    // ChatView's mount effects chain off these; auto-mocked they return undefined.
    mocked.listDocuments.mockResolvedValue({ documents: [] } as never);
    mocked.getMessageCount.mockResolvedValue({ count: 0 } as never);

    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    useChatStore.setState({
        sessions: [], currentSessionId: null, messages: [], agents: [], pendingPrompt: null,
        draftPermissionMode: 'ask', defaultPermissionMode: 'ask', showMemoryDashboard: false, showSchedules: false, settingsSection: null,
        draftDocumentIds: [],
    });
});

afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
});

async function advance(ms: number) {
    await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

function fakeTimers() {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
}

const BUSY: Partial<SystemStatus> = {
    lemonade_running: false,
    lemonade_error: 'Lemonade health query failed',
    start_command: 'lemond --start',
};

describe('system status poll', () => {
    it('never has more than one status request in flight', async () => {
        fakeTimers();
        let pending = 0;
        let maxPending = 0;
        mocked.getSystemStatus.mockImplementation(() => {
            pending++;
            maxPending = Math.max(maxPending, pending);
            // A backend stuck behind a busy model server: answers after 25 s.
            return new Promise((resolve) => setTimeout(() => {
                pending--;
                resolve({ lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF' } as never);
            }, 25_000));
        });
        render(<App />);
        await advance(120_000);
        expect(maxPending).toBe(1);
        expect(mocked.getSystemStatus.mock.calls.length).toBeLessThanOrEqual(5);
    });

    it('backs off while the model server is slow to answer', async () => {
        fakeTimers();
        mocked.getSystemStatus.mockResolvedValue(BUSY as never);
        render(<App />);
        await advance(60_000);
        // 5 s, then 10, 20, 30... instead of every 5 s.
        expect(mocked.getSystemStatus.mock.calls.length).toBeLessThanOrEqual(5);
    });

    it('says a busy model server is not answering instead of stopped', async () => {
        fakeTimers();
        mocked.getSystemStatus.mockResolvedValue(BUSY as never);
        render(<App />);
        await advance(120_000);
        expect(document.body.textContent).toMatch(/not answering/i);
        expect(document.body.textContent).not.toMatch(/stopped responding/i);
    });

    it('reports the backend lost only after three status requests in a row get no answer', async () => {
        fakeTimers();
        mocked.getSystemStatus.mockImplementation((signal?: AbortSignal) => new Promise((_resolve, reject) => {
            signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
        }));
        useChatStore.setState({ backendConnected: true });
        render(<App />);
        // Two timeouts (30 s each, plus backoff between them): still connected.
        await advance(75_000);
        expect(useChatStore.getState().backendConnected).toBe(true);
        expect(mocked.getSystemStatus.mock.calls.length).toBe(2);
        await advance(60_000);
        expect(mocked.getSystemStatus.mock.calls.length).toBe(3);
        expect(useChatStore.getState().backendConnected).toBe(false);
    });

    it('still tells the user how to start a model server that is down', async () => {
        fakeTimers();
        mocked.getSystemStatus.mockResolvedValue({ lemonade_running: false, lemonade_error: null, start_command: 'lemond --start' } as never);
        render(<App />);
        await advance(20_000);
        expect(document.body.textContent).toMatch(/stopped responding/i);
        expect(document.body.textContent).toContain('lemond --start');
    });
});
