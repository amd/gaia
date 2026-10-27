// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The desktop window loads before the backend listens, so the first requests
 * are refused. That is "still starting", not an error: the startup screen stays
 * up until the backend answers, then the chat UI appears with its chats.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore } from '../stores/modelStore';
import type { Session, SetupCheck } from '../types';

const mocked = vi.mocked(api);

function session(over: Partial<Session> = {}): Session {
    return {
        id: 'sess-old-0001',
        title: 'Trip planning',
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

let backendUp = false;
function whenUp<T>(value: T): () => Promise<T> {
    return () => (backendUp ? Promise.resolve(value) : Promise.reject(new TypeError('Failed to fetch')));
}

beforeEach(() => {
    backendUp = false;
    vi.stubGlobal('EventSource', class {
        close() { /* noop */ }
        addEventListener() { /* noop */ }
    });

    mocked.getActiveModel.mockImplementation(whenUp({
        provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true,
    }));
    mocked.checkSetup.mockImplementation(whenUp(READY));
    mocked.getSystemStatus.mockImplementation(whenUp({
        lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF', initialized: true,
    } as never));
    mocked.listAgents.mockImplementation(whenUp({ agents: [], total: 0 }));
    mocked.listSessions.mockImplementation(whenUp({ sessions: [session()], total: 1 }));
    mocked.listAllPermissions.mockImplementation(whenUp({ default_mode: 'ask' as const, sessions: [] }));
    mocked.getStartScreen.mockImplementation(whenUp({ cards: [], working_folder: { path: '/w', name: 'w', gaia_folder: false } }));
    mocked.listDocuments.mockImplementation(whenUp({ documents: [] } as never));
    mocked.getActiveRuns.mockImplementation(whenUp({ session_ids: [], queued: [], awaiting_user: {} }));
    mocked.getTunnelStatus.mockImplementation(whenUp({ active: false } as never));
    mocked.requestWarmUp.mockImplementation(whenUp({ state: 'ready' as const }));

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

describe('startup before the backend listens', () => {
    it('shows "Starting GAIA" until the backend answers, then the chats, with no error on the way', async () => {
        render(<App />);
        const seen: string[] = [];
        for (let t = 0; t < 1_500; t += 250) {
            await advance(250);
            seen.push(text());
        }
        expect(text()).toContain('Starting GAIA');

        backendUp = true;
        for (let t = 0; t < 20_000; t += 250) {
            await advance(250);
            seen.push(text());
        }

        expect(text()).not.toContain('Starting GAIA');
        expect(text()).toContain('Trip planning');
        for (const screen of seen) {
            expect(screen).not.toMatch(/Could not check whether GAIA is set up/);
            expect(screen).not.toMatch(/assistant didn/);
            expect(screen).not.toMatch(/No chats yet/);
            expect(screen).not.toMatch(/Lost the connection/i);
        }
    });

    it('opens within about a second of a slow backend starting to listen', async () => {
        render(<App />);
        await advance(20_000);
        expect(text()).toContain('Starting GAIA');

        backendUp = true;
        await advance(1_500);
        expect(text()).not.toContain('Starting GAIA');
        expect(text()).toContain('Trip planning');
    });

    it('still shows the setup banner when the backend answers with an HTTP error', async () => {
        backendUp = true;
        mocked.checkSetup.mockRejectedValue(
            Object.assign(new Error('Server error. Please try again.'), { name: 'ApiError', httpStatus: 500, detail: 'boom' }),
        );
        render(<App />);
        await advance(500);
        expect(text()).toMatch(/Could not check whether GAIA is set up: Server error/);
    });

    it('shows the setup banner when the backend never starts', async () => {
        render(<App />);
        await advance(30_000);
        expect(text()).toContain('Starting GAIA');
        await advance(60_000);
        expect(text()).toMatch(/Could not check whether GAIA is set up: Failed to fetch/);
    });
});

describe('"Check again" on the setup banner', () => {
    async function bannerAfterNeverStarting() {
        render(<App />);
        await advance(61_000);
        expect(text()).toMatch(/Could not check whether GAIA is set up: Failed to fetch/);
    }

    it('answers at once while the backend is still down, instead of waiting another 60 s', async () => {
        await bannerAfterNeverStarting();
        const before = mocked.checkSetup.mock.calls.length;
        fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
        await advance(500);
        expect(text()).toMatch(/Could not check whether GAIA is set up: Failed to fetch/);
        await advance(5_000);
        // One click is one request; it does not start a background retry loop.
        expect(mocked.checkSetup.mock.calls.length).toBe(before + 1);
    });

    it('repeated clicks while down each answer once and never stack retry loops', async () => {
        await bannerAfterNeverStarting();
        const before = mocked.checkSetup.mock.calls.length;
        for (let i = 0; i < 3; i++) {
            fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
            await advance(100);
        }
        await advance(10_000);
        expect(mocked.checkSetup.mock.calls.length).toBe(before + 3);
        expect(text()).toMatch(/Could not check whether GAIA is set up/);
    });

    it('clears the banner once the backend is up', async () => {
        await bannerAfterNeverStarting();
        backendUp = true;
        fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
        await advance(500);
        expect(text()).not.toMatch(/Could not check whether GAIA is set up/);
    });
});
