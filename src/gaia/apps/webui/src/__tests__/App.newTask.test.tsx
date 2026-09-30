// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Pins what the shell sends when a chat starts, and when it shows first-run setup.

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
const NOT_READY: SetupCheck = {
    ready: false,
    stage: 'setup',
    reasons: ['The local model server is not installed'],
    steps: [
        { key: 'server', label: 'Install the model server', detail: '' },
        { key: 'models', label: 'Download models', detail: '' },
    ],
};

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
    mocked.sendMessageStream.mockImplementation(() => new AbortController());
    mocked.getOnboardingPreflight.mockResolvedValue({
        compatible: true, blockers: [], warnings: [], ram_gb: 32, gpu_name: null, npu_detected: false, disk_free_gb: 100,
    } as never);
    mocked.getSetupStatus.mockResolvedValue({ state: 'idle' });
    // ChatView's mount effects chain off these; auto-mocked they return undefined.
    mocked.listDocuments.mockResolvedValue({ documents: [] } as never);
    mocked.getMessageCount.mockResolvedValue({ count: 0 } as never);

    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    useChatStore.setState({
        sessions: [], currentSessionId: null, messages: [], agents: [], pendingPrompt: null,
        draftPermissionMode: 'ask', showMemoryDashboard: false, showSchedules: false, settingsSection: null,
    });
});

afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
});

async function sendFirstMessage(text: string) {
    const box = await screen.findByLabelText('Message');
    await waitFor(() => expect(box).toBeEnabled());
    await userEvent.type(box, text);
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
}

describe('a new chat', () => {
    it('creates the session on the flagship agent with its first message', async () => {
        render(<App />);
        await sendFirstMessage('hello there');

        await waitFor(() => expect(mocked.createSession).toHaveBeenCalledOnce());
        expect(mocked.createSession.mock.calls[0][0]).toMatchObject({ title: 'New Task', agent_type: 'gaia' });
        await waitFor(() => expect(mocked.sendMessageStream).toHaveBeenCalled());
        expect(mocked.sendMessageStream.mock.calls[0].slice(0, 2)).toEqual(['sess-new-0001', 'hello there']);
    });

    it('still uses the flagship after viewing a legacy chat', async () => {
        const legacy = session({ agent_type: 'doc' });
        mocked.listSessions.mockResolvedValue({ sessions: [legacy], total: 1 });
        useChatStore.setState({ sessions: [legacy], currentSessionId: legacy.id });

        render(<App />);
        await userEvent.click(await screen.findByRole('button', { name: 'New chat' }));
        await sendFirstMessage('new question');

        await waitFor(() => expect(mocked.createSession).toHaveBeenCalledOnce());
        expect(mocked.createSession.mock.calls[0][0]).toMatchObject({ agent_type: 'gaia' });
        // Creating a new chat must never rewrite an existing conversation.
        expect(mocked.updateSession).not.toHaveBeenCalledWith(legacy.id, expect.anything());
        expect(useChatStore.getState().sessions.find((s) => s.id === legacy.id)?.agent_type).toBe('doc');
    });

    it('applies a permission mode picked before the chat existed', async () => {
        useChatStore.setState({ draftPermissionMode: 'full_access' });
        render(<App />);
        await sendFirstMessage('go');

        await waitFor(() => expect(mocked.setPermissionMode).toHaveBeenCalledWith('sess-new-0001', 'full_access'));
        expect(useChatStore.getState().draftPermissionMode).toBe('ask');
    });

    it('sends "ask" too, so a full-access default in config cannot hide behind the chip', async () => {
        render(<App />);
        await sendFirstMessage('go');

        await waitFor(() => expect(mocked.setPermissionMode).toHaveBeenCalledWith('sess-new-0001', 'ask'));
    });

    it('does not send the message when the permission mode could not be set', async () => {
        mocked.setPermissionMode.mockRejectedValue(new Error('backend said no'));
        render(<App />);
        await sendFirstMessage('go');

        expect(await screen.findByRole('alert')).toHaveTextContent(/permission mode could not be set.*backend said no/);
        expect(mocked.sendMessageStream).not.toHaveBeenCalled();
    });
});

describe('first-run setup', () => {
    it('shows the setup screen when GAIA is not set up', async () => {
        mocked.checkSetup.mockResolvedValue(NOT_READY);
        render(<App />);

        expect(await screen.findByRole('heading', { name: 'Set up GAIA' })).toBeInTheDocument();
        expect(screen.getByText('The local model server is not installed.')).toBeInTheDocument();
        expect(screen.queryByLabelText('Message')).not.toBeInTheDocument();
    });

    it('goes straight to the chat when GAIA is ready', async () => {
        render(<App />);

        await waitFor(() => expect(mocked.checkSetup).toHaveBeenCalledWith(false));
        await waitFor(() => expect(screen.getByLabelText('Message')).toBeEnabled());
        expect(screen.queryByRole('heading', { name: 'Set up GAIA' })).not.toBeInTheDocument();
    });

    it('skips the local chat model check when a cloud model is active', async () => {
        mocked.getActiveModel.mockResolvedValue({
            provider: 'fireworks', model: 'fireworks.accounts/fireworks/models/kimi', label: 'Kimi', remote: true, is_default: false,
        });
        render(<App />);
        await waitFor(() => expect(mocked.checkSetup).toHaveBeenCalledWith(true));
    });
});
