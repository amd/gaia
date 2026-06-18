// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * App.tsx nav-guard tests — URL/hash session navigation (issues #1086, #1755).
 *
 * Renders the real <App> with every child stubbed so only App's own effects
 * run, then drives the URL-hash navigation guard with fake timers.
 */

import { render, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { getSessionHash } from '../utils/format';

// Stub every child so their mount effects don't interfere with the guard.
vi.mock('../components/Sidebar', () => ({ Sidebar: () => null }));
vi.mock('../components/ChatView', () => ({ ChatView: () => null }));
vi.mock('../components/WelcomeScreen', () => ({ WelcomeScreen: () => null }));
vi.mock('../components/DocumentLibrary', () => ({ DocumentLibrary: () => null }));
vi.mock('../components/FileBrowser', () => ({ FileBrowser: () => null }));
vi.mock('../components/MemoryDashboard', () => ({ MemoryDashboard: () => null }));
vi.mock('../components/ScheduleManager', () => ({ ScheduleManager: () => null }));
vi.mock('../components/SettingsPage', () => ({ SettingsPage: () => null }));
vi.mock('../components/MobileAccessModal', () => ({ MobileAccessModal: () => null }));
vi.mock('../components/ConnectionBanner', () => ({ ConnectionBanner: () => null }));
vi.mock('../components/UpdateIndicator', () => ({ UpdateIndicator: () => null }));
vi.mock('../components/PermissionPrompt', () => ({ PermissionPrompt: () => null }));
vi.mock('../components/NotificationCenter', () => ({ NotificationCenter: () => null }));
vi.mock('../services/api');

const SESSION_A = { id: 'aaaaaaaa-1111-2222-3333-444444444444' };
const SESSION_B = { id: 'bbbbbbbb-5555-6666-7777-888888888888' };

const mockedApi = vi.mocked(api);

// jsdom has no EventSource; provide a no-op so App's SSE effect can mount.
class FakeEventSource {
    onopen: (() => void) | null = null;
    onmessage: ((ev: { data: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    close() { /* no-op */ }
}

beforeEach(() => {
    vi.clearAllMocks();
    (globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource;

    mockedApi.listAgents.mockResolvedValue({ agents: [] } as never);
    mockedApi.getSystemStatus.mockResolvedValue({ lemonade_running: true } as never);
    mockedApi.listSessions.mockResolvedValue({ sessions: [SESSION_A, SESSION_B] } as never);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] } as never);
    mockedApi.getTunnelStatus.mockResolvedValue({ active: false } as never);

    useChatStore.setState({
        sessions: [SESSION_A, SESSION_B] as never,
        currentSessionId: null,
        messages: [],
    });
    window.history.replaceState(null, '', '/');
});

afterEach(() => {
    vi.useRealTimers();
});

describe('App URL/hash session navigation guard', () => {
    it('navigates to the session named in the URL hash on mount', async () => {
        vi.useFakeTimers();
        window.history.replaceState(null, '', `#${getSessionHash(SESSION_A.id)}`);

        await act(async () => {
            render(<App />);
        });
        await act(async () => {
            await vi.advanceTimersByTimeAsync(600);
        });

        expect(useChatStore.getState().currentSessionId).toBe(SESSION_A.id);
    });

    it('does not ping-pong back to the URL hash after switching sessions (#1755)', async () => {
        vi.useFakeTimers();
        window.history.replaceState(null, '', `#${getSessionHash(SESSION_A.id)}`);

        await act(async () => {
            render(<App />);
        });
        await act(async () => {
            await vi.advanceTimersByTimeAsync(600);
        });
        expect(useChatStore.getState().currentSessionId).toBe(SESSION_A.id);

        // User switches to B (e.g. sidebar click). The hash-sync effect rewrites
        // the URL hash to B's short hash. The nav guard must NOT re-fire and bounce
        // back to A — that was the #1755 oscillation.
        await act(async () => {
            useChatStore.getState().setCurrentSession(SESSION_B.id);
        });
        await act(async () => {
            await vi.advanceTimersByTimeAsync(600);
        });

        expect(useChatStore.getState().currentSessionId).toBe(SESSION_B.id);
    });
});
