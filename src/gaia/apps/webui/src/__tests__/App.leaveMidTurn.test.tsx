// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Leaving a chat for Memory or Scheduled tasks mid-turn, then coming back,
// still shows the turn's answer.

import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore } from '../stores/modelStore';
import type { Session, StreamEvent } from '../types';

const mocked = vi.mocked(api);

const CHAT: Session = {
    id: 'sess-running-0001', title: 'Long answer', created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z', model: '', system_prompt: null, message_count: 1,
    document_ids: [], agent_type: 'gaia',
};

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
    mocked.getStartScreen.mockResolvedValue({ cards: [], working_folder: { path: '/w', name: 'w', gaia_folder: false } });
    mocked.getSetupPlan.mockRejectedValue(new Error('no plan'));
    mocked.getSystemStatus.mockResolvedValue({ lemonade_running: true, model_loaded: 'Gemma-4-E4B-it-GGUF' } as never);
    mocked.listAgents.mockResolvedValue({ agents: [], total: 0 });
    mocked.listSessions.mockResolvedValue({ sessions: [CHAT], total: 1 });
    mocked.getActiveRuns.mockResolvedValue({ session_ids: [CHAT.id], queued: [], awaiting_user: {} });
    mocked.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mocked.getTunnelStatus.mockResolvedValue({ active: false } as never);
    mocked.getMessages.mockResolvedValue({ messages: [] } as never);
    mocked.getPermissions.mockResolvedValue({ session_id: CHAT.id, mode: 'ask', grants: [] });
    mocked.listAllPermissions.mockResolvedValue({ default_mode: 'ask', sessions: [] });
    mocked.getSetupStatus.mockResolvedValue({ state: 'idle' });
    mocked.listDocuments.mockResolvedValue({ documents: [] } as never);
    mocked.getMessageCount.mockResolvedValue({ count: 0 } as never);
    mocked.attachToRun.mockImplementation((_sid, cbs) => {
        attaches.push(cbs as api.StreamCallbacks);
        return new AbortController();
    });

    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    useChatStore.setState({
        sessions: [CHAT], currentSessionId: CHAT.id, messages: [], agents: [], pendingPrompt: null,
        draftPermissionMode: 'ask', defaultPermissionMode: 'ask', showMemoryDashboard: false, showSchedules: false,
        settingsSection: null, draftDocumentIds: [], draftPrivate: false,
        isStreaming: false, streamingContent: '', agentSteps: [], cards: [],
    });
});

afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
});

describe.each([
    ['Memory', 'setShowMemoryDashboard'],
    ['Scheduled tasks', 'setShowSchedules'],
] as const)('coming back from %s while the turn still runs', (_page, setter) => {
    it('re-attaches once and shows the answer', async () => {
        render(<App />);
        await waitFor(() => expect(attaches).toHaveLength(1));
        act(() => attaches[0].onChunk({ type: 'chunk', content: 'Partial' } as unknown as StreamEvent));

        act(() => useChatStore.getState()[setter](true));
        act(() => useChatStore.getState()[setter](false));

        await waitFor(() => expect(attaches).toHaveLength(2));
        act(() => attaches[1].onDone({ type: 'done', message_id: 7, content: 'Final answer.' } as unknown as StreamEvent));
        expect(await screen.findByText('Final answer.')).toBeInTheDocument();
        expect(attaches).toHaveLength(2);
    });
});
