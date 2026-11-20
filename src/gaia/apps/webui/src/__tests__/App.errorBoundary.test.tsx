// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A crashed main area clears when the user leaves it or asks for a new chat (#6956).

import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');
vi.mock('../services/memoryApi');
vi.mock('../components/MemoryDashboard', () => ({
    MemoryDashboard: () => { throw new Error('memory dashboard broke'); },
}));

let newChatBroken = false;
vi.mock('../components/NewChat', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../components/NewChat')>();
    return {
        NewChat: (props: Parameters<typeof actual.NewChat>[0]) => {
            if (newChatBroken) throw new Error('new chat broke');
            return <actual.NewChat {...props} />;
        },
    };
});

import App from '../App';
import * as api from '../services/api';
import * as memoryApi from '../services/memoryApi';
import { useChatStore } from '../stores/chatStore';
import { useModelStore } from '../stores/modelStore';

const mocked = vi.mocked(api);
beforeEach(() => {
    // App opens an SSE channel on mount; jsdom has no EventSource.
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
    mocked.getSystemStatus.mockResolvedValue({
        lemonade_running: true,
        model_loaded: 'Gemma-4-E4B-it-GGUF',
    } as never);
    mocked.listAgents.mockResolvedValue({ agents: [], total: 0 });
    mocked.listSessions.mockResolvedValue({ sessions: [], total: 0 });
    mocked.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mocked.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mocked.getTunnelStatus.mockResolvedValue({ active: false } as never);
    mocked.createSession.mockRejectedValue(new Error('not used'));
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
    vi.mocked(memoryApi.getMemorySettings).mockResolvedValue({
        memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false,
    });

    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    useChatStore.setState({
        sessions: [], currentSessionId: null, messages: [], agents: [], pendingPrompt: null,
        draftPermissionMode: 'ask', defaultPermissionMode: 'ask', showMemoryDashboard: false, showSchedules: false, settingsSection: null,
        draftDocumentIds: [], draftPrivate: false, locale: 'en',
    });
});

let consoleError: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
    newChatBroken = false;
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
    consoleError.mockRestore();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
});

describe('a crashed main area', () => {
    it('clears when the Memory dashboard is closed', async () => {
        useChatStore.setState({ showMemoryDashboard: true });
        render(<App />);
        expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong');

        act(() => useChatStore.getState().setShowMemoryDashboard(false));
        expect(await screen.findByLabelText('Message')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('clears when New chat is clicked after the new-chat screen crashed', async () => {
        newChatBroken = true;
        render(<App />);
        expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong');

        newChatBroken = false;
        await userEvent.click(screen.getByRole('button', { name: 'New chat' }));
        expect(await screen.findByLabelText('Message')).toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('keeps a healthy new chat mounted, with its typed text, on New chat again', async () => {
        render(<App />);
        const box = await screen.findByLabelText('Message');
        await waitFor(() => expect(box).toBeEnabled());
        await userEvent.type(box, 'half a thought');

        await userEvent.click(screen.getByRole('button', { name: 'New chat' }));
        expect(screen.getByLabelText('Message')).toBe(box);
        expect(box).toHaveValue('half a thought');
    });
});
