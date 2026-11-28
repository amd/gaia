// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// One chat waiting on an approval card must not take the next chat's messages:
// New chat still opens, and what is typed there starts a chat of its own.

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore } from '../stores/modelStore';
import { useNotificationStore } from '../stores/notificationStore';
import type { Session, StreamEvent } from '../types';

const mocked = vi.mocked(api);

const WAITING: Session = {
    id: 'sess-waiting-0001', title: 'Password lengths', created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z', model: '', system_prompt: null, message_count: 1,
    document_ids: [], agent_type: 'gaia',
};
const CREATED: Session = { ...WAITING, id: 'sess-new-0002', title: 'New chat', message_count: 0 };

let attaches: api.StreamCallbacks[];

beforeEach(() => {
    attaches = [];
    vi.stubGlobal('EventSource', class {
        close() { /* noop */ }
        addEventListener() { /* noop */ }
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });

    mocked.getActiveModel.mockResolvedValue({
        provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true,
    });
    mocked.checkSetup.mockResolvedValue({ ready: true, steps: [] });
    mocked.requestWarmUp.mockResolvedValue({ state: 'ready' });
    mocked.getStartScreen.mockResolvedValue({ cards: [], working_folder: { path: '/w', name: 'w', gaia_folder: false, choosable: true } });
    mocked.getSetupPlan.mockRejectedValue(new Error('no plan'));
    mocked.getSystemStatus.mockResolvedValue({ lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF' } as never);
    mocked.listAgents.mockResolvedValue({ agents: [], total: 0 });
    mocked.listSessions.mockResolvedValue({ sessions: [WAITING], total: 1 });
    mocked.getActiveRuns.mockResolvedValue({ session_ids: [WAITING.id], queued: [], awaiting_user: {} });
    mocked.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mocked.getTunnelStatus.mockResolvedValue({ active: false } as never);
    mocked.getMessages.mockResolvedValue({ messages: [] } as never);
    mocked.getPermissions.mockResolvedValue({ session_id: WAITING.id, mode: 'ask', grants: [] });
    mocked.listAllPermissions.mockResolvedValue({ default_mode: 'ask', sessions: [] });
    mocked.getSetupStatus.mockResolvedValue({ state: 'idle' });
    mocked.listDocuments.mockResolvedValue({ documents: [] } as never);
    mocked.getMessageCount.mockResolvedValue({ count: 0 } as never);
    mocked.createSession.mockResolvedValue(CREATED);
    mocked.setPermissionMode.mockResolvedValue(undefined as never);
    mocked.attachToRun.mockImplementation((_sid, cbs) => {
        attaches.push(cbs as api.StreamCallbacks);
        return new AbortController();
    });
    mocked.sendMessageStream.mockImplementation(() => new AbortController());

    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    useNotificationStore.setState({ notifications: [] });
    useChatStore.setState({
        sessions: [WAITING], currentSessionId: WAITING.id, messages: [], agents: [], pendingPrompt: null,
        draftPermissionMode: 'ask', defaultPermissionMode: 'ask', showMemoryDashboard: false, showSchedules: false,
        settingsSection: null, draftDocumentIds: [], draftPrivate: false, messageQueues: {},
        isStreaming: false, streamingContent: '', agentSteps: [], cards: [],
    });
});

afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
});

describe('New chat while another chat waits on an approval card', () => {
    it('starts its own chat and leaves the waiting chat with nothing queued', async () => {
        render(<App />);
        await waitFor(() => expect(attaches).toHaveLength(1));
        act(() => attaches[0].onAgentEvent({
            type: 'permission_request', tool: 'run_shell_command', tool_label: 'Run shell',
            confirm_id: 'perm-1', args: { command: 'python3 -c "import pypdf"' },
        } as unknown as StreamEvent));
        expect(await screen.findByRole('heading', { name: 'Run this command?' })).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: /New chat/ }));
        await waitFor(() => expect(useChatStore.getState().currentSessionId).toBeNull());
        expect(screen.queryByRole('heading', { name: 'Run this command?' })).not.toBeInTheDocument();

        const box = await screen.findByRole('textbox');
        fireEvent.change(box, { target: { value: 'Which signature algorithms does NIST name?' } });
        fireEvent.keyDown(box, { key: 'Enter' });

        await waitFor(() => expect(mocked.createSession).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(useChatStore.getState().currentSessionId).toBe(CREATED.id));
        expect(useChatStore.getState().messageQueues[WAITING.id]).toBeUndefined();
        // The waiting chat still asks, in its own row.
        expect(useNotificationStore.getState().notifications.some(
            (n) => n.type === 'permission_request' && n.sessionId === WAITING.id && !n.response && !n.dismissed,
        )).toBe(true);
    });
});
