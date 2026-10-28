// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Leaving a chat mid-turn for another chat, Memory or Scheduled tasks, then
// coming back, still shows the turn live and then its answer.

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
    mocked.getStartScreen.mockResolvedValue({ cards: [], working_folder: { path: '/w', name: 'w', gaia_folder: false, choosable: true } });
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

const OTHER: Session = { ...CHAT, id: 'sess-idle-0002', title: 'Other chat' };

// The server once the turn ends: no longer running, and the answer saved.
function finishRun() {
    mocked.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mocked.getMessages.mockResolvedValue({ messages: [{
        id: 7, session_id: CHAT.id, role: 'assistant', content: 'Final answer.',
        created_at: '2026-01-01T00:00:01Z', rag_sources: null,
    }] } as never);
}

async function startTurn() {
    render(<App />);
    await waitFor(() => expect(attaches).toHaveLength(1));
    act(() => attaches[0].onChunk({ type: 'chunk', content: 'Partial' } as unknown as StreamEvent));
}

describe('coming back from Memory while the turn still runs', () => {
    it('re-attaches once and shows the answer', async () => {
        await startTurn();

        act(() => useChatStore.getState().setShowMemoryDashboard(true));
        act(() => useChatStore.getState().setShowMemoryDashboard(false));

        await waitFor(() => expect(attaches).toHaveLength(2));
        expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument();
        finishRun();
        act(() => attaches[1].onDone({ type: 'done', message_id: 7, content: 'Final answer.' } as unknown as StreamEvent));
        expect(await screen.findByText('Final answer.')).toBeInTheDocument();
        expect(attaches).toHaveLength(2);
    });
});

describe('closing Scheduled tasks while the turn still runs', () => {
    it('keeps the answer streaming without re-attaching', async () => {
        render(<App />);
        await waitFor(() => expect(attaches).toHaveLength(1));
        act(() => attaches[0].onChunk({ type: 'chunk', content: 'Partial' } as unknown as StreamEvent));

        act(() => useChatStore.getState().setShowSchedules(true));
        act(() => useChatStore.getState().setShowSchedules(false));

        expect(await screen.findByText('Partial')).toBeInTheDocument();
        expect(useChatStore.getState().isStreaming).toBe(true);
        expect(attaches).toHaveLength(1);
    });
});

describe('switching to another chat and back while the turn still runs', () => {
    beforeEach(() => {
        mocked.listSessions.mockResolvedValue({ sessions: [CHAT, OTHER], total: 2 });
        useChatStore.setState({ sessions: [CHAT, OTHER] });
    });

    it('shows the turn live again, with Stop', async () => {
        await startTurn();

        act(() => useChatStore.getState().setCurrentSession(OTHER.id));
        expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument();
        act(() => useChatStore.getState().setCurrentSession(CHAT.id));

        await waitFor(() => expect(attaches).toHaveLength(2));
        expect(await screen.findByRole('button', { name: 'Stop' })).toBeInTheDocument();
        finishRun();
        act(() => attaches[1].onDone({ type: 'done', message_id: 7, content: 'Final answer.' } as unknown as StreamEvent));
        expect(await screen.findByText('Final answer.')).toBeInTheDocument();
        expect(attaches).toHaveLength(2);
    });

    it('keeps waiting on an approval prompt, with Stop', async () => {
        await startTurn();

        act(() => useChatStore.getState().setCurrentSession(OTHER.id));
        act(() => useChatStore.getState().setCurrentSession(CHAT.id));

        await waitFor(() => expect(attaches).toHaveLength(2));
        act(() => attaches[1].onAgentEvent({
            type: 'permission_request', tool: 'run_shell_command', tool_label: 'Run shell',
            confirm_id: 'perm-1', args: { command: 'ls' },
        } as unknown as StreamEvent));
        expect((await screen.findAllByText('Allow Run shell?')).length).toBeGreaterThan(0);
        expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument();
    });
});

describe('switching chats mid-turn: edge cases', () => {
    beforeEach(() => {
        mocked.listSessions.mockResolvedValue({ sessions: [CHAT, OTHER], total: 2 });
        useChatStore.setState({ sessions: [CHAT, OTHER] });
        mocked.cancelStream.mockResolvedValue(undefined as never);
    });

    it('shows the live text once and the final answer once', async () => {
        await startTurn();
        act(() => useChatStore.getState().setCurrentSession(OTHER.id));
        act(() => useChatStore.getState().setCurrentSession(CHAT.id));
        await waitFor(() => expect(attaches).toHaveLength(2));

        act(() => attaches[1].onChunk({ type: 'chunk', content: 'Live text' } as unknown as StreamEvent));
        expect(await screen.findByText('Live text')).toBeInTheDocument();
        finishRun();
        act(() => attaches[1].onDone({ type: 'done', message_id: 7, content: 'Final answer.' } as unknown as StreamEvent));
        await waitFor(() => expect(screen.getAllByText('Final answer.')).toHaveLength(1));
        expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument();
        expect(useChatStore.getState().isStreaming).toBe(false);
    });

    it('does not re-attach or show Stop when the turn ended while away', async () => {
        await startTurn();
        act(() => useChatStore.getState().setCurrentSession(OTHER.id));
        finishRun();
        act(() => useChatStore.getState().setCurrentSession(CHAT.id));

        // The last poll still listed the run, so the view may attach once; the
        // server answers a finished run with 404, which attachToRun maps to an empty done.
        for (const a of attaches.slice(1)) {
            act(() => a.onDone({ type: 'done', content: '' } as unknown as StreamEvent));
        }
        await waitFor(() => expect(screen.getAllByText('Final answer.')).toHaveLength(1));
        expect(attaches.length).toBeLessThanOrEqual(2);
        await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument());
        expect(useChatStore.getState().isStreaming).toBe(false);
    });

    it('rapid switches leave the idle chat idle and the running chat live', async () => {
        await startTurn();
        act(() => useChatStore.getState().setCurrentSession(OTHER.id));
        act(() => useChatStore.getState().setCurrentSession(CHAT.id));
        act(() => useChatStore.getState().setCurrentSession(OTHER.id));
        await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
        expect(useChatStore.getState().isStreaming).toBe(false);
        expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument();

        // A late chunk from the abandoned stream must not leak into the other chat.
        const last = attaches[attaches.length - 1];
        act(() => last.onChunk({ type: 'chunk', content: 'Leaked' } as unknown as StreamEvent));
        expect(screen.queryByText('Leaked')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument();

        const before = attaches.length;
        act(() => useChatStore.getState().setCurrentSession(CHAT.id));
        await waitFor(() => expect(attaches.length).toBe(before + 1));
        expect(await screen.findByRole('button', { name: 'Stop' })).toBeInTheDocument();
    });

    it('Stop after coming back cancels the right run and closes the turn', async () => {
        await startTurn();
        act(() => useChatStore.getState().setCurrentSession(OTHER.id));
        act(() => useChatStore.getState().setCurrentSession(CHAT.id));
        await waitFor(() => expect(attaches).toHaveLength(2));

        act(() => screen.getByRole('button', { name: 'Stop' }).click());
        expect(mocked.cancelStream).toHaveBeenCalledWith(CHAT.id);
        finishRun();
        act(() => attaches[1].onDone({ type: 'done', message_id: 7, content: 'Final answer.' } as unknown as StreamEvent));
        expect(await screen.findByText('Final answer.')).toBeInTheDocument();
        await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument());
        expect(useChatStore.getState().isStreaming).toBe(false);
    });
});
