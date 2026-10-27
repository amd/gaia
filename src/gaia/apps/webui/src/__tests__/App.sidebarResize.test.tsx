// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Narrowing the window, opening the drawer, then widening again brings the
// desktop sidebar back (#5930).

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore } from '../stores/modelStore';

const mocked = vi.mocked(api);
const PHONE = '(max-width: 768px)';

let width = 1400;
const listeners = new Set<() => void>();

function resize(to: number) {
    width = to;
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: to });
    act(() => listeners.forEach((fn) => fn()));
}

beforeEach(() => {
    width = 1400;
    listeners.clear();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
    Object.defineProperty(window, 'matchMedia', {
        configurable: true,
        value: (query: string) => ({
            get matches() { return query === PHONE && width <= 768; },
            media: query,
            addEventListener: (_: string, fn: () => void) => { if (query === PHONE) listeners.add(fn); },
            removeEventListener: (_: string, fn: () => void) => { listeners.delete(fn); },
        }),
    });
    vi.stubGlobal('EventSource', class {
        close() { /* noop */ }
        addEventListener() { /* noop */ }
    });

    mocked.getActiveModel.mockResolvedValue({
        provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true,
    });
    mocked.checkSetup.mockResolvedValue({ ready: true, steps: [] });
    mocked.requestWarmUp.mockResolvedValue({ state: 'ready' });
    mocked.getStartScreen.mockResolvedValue({ cards: [], working_folder: { path: '/w', name: 'w', gaia_folder: false, choosable: false } });
    mocked.getSetupPlan.mockRejectedValue(new Error('no plan'));
    mocked.getSystemStatus.mockResolvedValue({ lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF' } as never);
    mocked.listAgents.mockResolvedValue({ agents: [], total: 0 });
    mocked.listSessions.mockResolvedValue({ sessions: [], total: 0 });
    mocked.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mocked.getTunnelStatus.mockResolvedValue({ active: false } as never);
    mocked.listAllPermissions.mockResolvedValue({ default_mode: 'ask', sessions: [] });
    mocked.getSetupStatus.mockResolvedValue({ state: 'idle' });
    mocked.listDocuments.mockResolvedValue({ documents: [] } as never);

    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    useChatStore.setState({
        sessions: [], currentSessionId: null, messages: [], agents: [], pendingPrompt: null,
        showMemoryDashboard: false, showSchedules: false, settingsSection: null,
        sidebarOpen: true, sidebarCollapsed: false,
    });
});

afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    delete (window as { matchMedia?: unknown }).matchMedia;
});

describe('sidebar after a narrow-then-wide resize', () => {
    it('returns to the desktop layout with Mobile access and the collapse button', async () => {
        render(<App />);
        expect(await screen.findByRole('button', { name: 'Mobile access' })).toBeInTheDocument();

        resize(700);
        fireEvent.click(screen.getByRole('button', { name: 'Open sidebar' }));
        expect(screen.queryByRole('button', { name: 'Mobile access' })).not.toBeInTheDocument();

        resize(1400);
        expect(screen.getByRole('button', { name: 'Mobile access' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Collapse sidebar' }).querySelector('.lucide-x')).toBeNull();
        expect(screen.queryByRole('button', { name: 'Open sidebar' })).not.toBeInTheDocument();
    });
});
