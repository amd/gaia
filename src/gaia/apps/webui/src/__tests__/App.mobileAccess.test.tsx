// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The session panel's Phone access switch waits for App to announce the
// outcome. A start or stop that fails or stops early must still announce, or
// the switch stays disabled on "Starting…".

import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore } from '../stores/modelStore';
import { log } from '../utils/logger';
import { requestMobileAccess } from '../utils/mobileAccessEvents';

const mocked = vi.mocked(api);
let announced: boolean[] = [];
const onState = (e: Event) => announced.push(Boolean((e as CustomEvent<boolean>).detail));

beforeEach(() => {
    vi.stubGlobal('EventSource', class {
        close() { /* noop */ }
        addEventListener() { /* noop */ }
    });
    mocked.getActiveModel.mockResolvedValue({
        provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true,
    });
    mocked.checkSetup.mockResolvedValue({ ready: true, steps: [] });
    mocked.requestWarmUp.mockResolvedValue({ state: 'ready' });
    mocked.getStartScreen.mockResolvedValue({ cards: [], working_folder: { path: '/w', name: 'w', gaia_folder: false, choosable: true } });
    mocked.getSetupPlan.mockRejectedValue(new Error('no plan'));
    mocked.getSystemStatus.mockResolvedValue({ lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF' } as never);
    mocked.listAgents.mockResolvedValue({ agents: [], total: 0 });
    mocked.listSessions.mockResolvedValue({ sessions: [], total: 0 });
    mocked.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mocked.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mocked.getTunnelStatus.mockResolvedValue({ active: false } as never);
    mocked.listAllPermissions.mockResolvedValue({ default_mode: 'ask', sessions: [] });
    mocked.getOnboardingPreflight.mockResolvedValue({
        compatible: true, blockers: [], warnings: [], ram_gb: 32, gpu_name: null, npu_detected: false, disk_free_gb: 100,
    } as never);
    mocked.getSetupStatus.mockResolvedValue({ state: 'idle' });
    mocked.getLemonadeTarget.mockResolvedValue({
        mode: 'gaia', saved_mode: null, source: 'gaia', base_url: null, saved_base_url: null,
        api_key_stored: false, gaia_installed: false, offer: false, what_gaia_does: '',
    });
    mocked.listDocuments.mockResolvedValue({ documents: [] } as never);

    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    useChatStore.setState({
        sessions: [], currentSessionId: null, messages: [], agents: [], pendingPrompt: null,
        settingsSection: null, showFileBrowser: false, isStreaming: false, streamingContent: '',
    });
    announced = [];
    window.addEventListener('gaia:mobile-access-state', onState);
});

afterEach(() => {
    window.removeEventListener('gaia:mobile-access-state', onState);
    window.history.replaceState(null, '', '/');
    vi.restoreAllMocks();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
});

async function renderReady(active: boolean) {
    mocked.getTunnelStatus.mockResolvedValue({ active } as never);
    render(<App />);
    await screen.findByLabelText('Message');
    await waitFor(() => expect(mocked.getTunnelStatus).toHaveBeenCalled());
    // Let the initial status settle, then watch only what the request announces.
    await act(async () => { await Promise.resolve(); });
    announced = [];
}

describe('Phone access requests always announce an outcome', () => {
    it('announces "off" when the tunnel fails to start', async () => {
        const errorSpy = vi.spyOn(log.system, 'error').mockImplementation(() => {});
        mocked.startTunnel.mockRejectedValue(new Error('ngrok exploded'));
        await renderReady(false);

        act(() => requestMobileAccess(true));

        await waitFor(() => expect(announced).toContain(false));
        expect(announced).not.toContain(true);
        expect(errorSpy).toHaveBeenCalledWith('Could not start mobile access', expect.any(Error));
        expect(await screen.findByText('ngrok exploded')).toBeInTheDocument();
    });

    it('announces "off" when ngrok is not installed', async () => {
        mocked.startTunnel.mockResolvedValue({ active: false, reason: 'ngrok_not_installed', installCommands: [] } as never);
        await renderReady(false);

        act(() => requestMobileAccess(true));

        await waitFor(() => expect(announced).toContain(false));
        expect(announced).not.toContain(true);
    });

    it('announces "on" when the tunnel starts', async () => {
        mocked.startTunnel.mockResolvedValue({ active: true } as never);
        await renderReady(false);

        act(() => requestMobileAccess(true));

        await waitFor(() => expect(announced).toContain(true));
    });

    it('announces "on" when stopping fails', async () => {
        const errorSpy = vi.spyOn(log.system, 'error').mockImplementation(() => {});
        mocked.stopTunnel.mockRejectedValue(new Error('stop failed'));
        await renderReady(true);

        act(() => requestMobileAccess(false));

        await waitFor(() => expect(announced).toContain(true));
        expect(announced).not.toContain(false);
        expect(errorSpy).toHaveBeenCalledWith('Could not stop mobile access', expect.any(Error));
    });
});
