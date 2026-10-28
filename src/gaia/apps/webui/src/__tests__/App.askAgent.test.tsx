// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Ask Agent in the file browser, end to end through App: the prompt about the
// file goes to exactly one chat, the one it was asked from (#5924).

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore } from '../stores/modelStore';
import type { BrowseResponse, Session } from '../types';

const mocked = vi.mocked(api);
const PROMPT = 'Please analyze this document for me: notes.txt';

function session(over: Partial<Session> = {}): Session {
    return {
        id: 'sess-other-0001',
        title: 'Other chat',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        model: '',
        system_prompt: null,
        message_count: 2,
        document_ids: [],
        agent_type: 'gaia',
        ...over,
    };
}

const OTHER = session();
const NEW = session({ id: 'sess-new-0001', title: 'New Task', message_count: 0 });

const BROWSE: BrowseResponse = {
    current_path: '/home/user/docs',
    parent_path: '/home/user',
    entries: [
        { name: 'notes.txt', path: '/home/user/docs/notes.txt', type: 'file', size: 512, extension: '.txt', modified: '' },
    ],
    quick_links: [],
};

const DOC = {
    id: 'doc-1', filename: 'notes.txt', filepath: '/home/user/docs/notes.txt', file_size: 512,
    chunk_count: 1, indexed_at: '', last_accessed_at: null, sessions_using: 0, indexing_status: 'complete',
};

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
    mocked.listSessions.mockResolvedValue({ sessions: [OTHER], total: 1 });
    mocked.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mocked.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mocked.getTunnelStatus.mockResolvedValue({ active: false } as never);
    mocked.createSession.mockResolvedValue(NEW);
    mocked.getMessages.mockResolvedValue({ messages: [] } as never);
    mocked.getPermissions.mockResolvedValue({ session_id: NEW.id, mode: 'ask', grants: [] });
    mocked.setPermissionMode.mockResolvedValue({ session_id: NEW.id, mode: 'ask', grants: [] });
    mocked.listAllPermissions.mockResolvedValue({ default_mode: 'ask', sessions: [] });
    // A stream that never finishes: a chat that is mid-answer stays mid-answer.
    mocked.sendMessageStream.mockImplementation(() => new AbortController());
    mocked.getOnboardingPreflight.mockResolvedValue({
        compatible: true, blockers: [], warnings: [], ram_gb: 32, gpu_name: null, npu_detected: false, disk_free_gb: 100,
    } as never);
    mocked.getSetupStatus.mockResolvedValue({ state: 'idle' });
    mocked.getLemonadeTarget.mockResolvedValue({
        mode: 'gaia', saved_mode: null, source: 'gaia', base_url: null, saved_base_url: null,
        api_key_stored: false, gaia_installed: false, offer: false, what_gaia_does: '',
    });
    mocked.listDocuments.mockResolvedValue({ documents: [DOC] } as never);
    mocked.getMessageCount.mockResolvedValue({ count: 0 } as never);
    mocked.browseFiles.mockResolvedValue(BROWSE);
    mocked.uploadDocumentByPath.mockResolvedValue(DOC as never);
    mocked.attachDocument.mockResolvedValue(undefined as never);

    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    useChatStore.setState({
        sessions: [OTHER], currentSessionId: null, messages: [], agents: [], pendingPrompt: null,
        draftPermissionMode: 'ask', defaultPermissionMode: 'ask', showMemoryDashboard: false, showSchedules: false,
        settingsSection: null, draftDocumentIds: [], draftPrivate: false, showFileBrowser: false,
        isStreaming: false, streamingContent: '', messageQueues: {},
    });
});

afterEach(() => {
    // Opening a chat writes its link to the hash; the next App would open it.
    window.history.replaceState(null, '', '/');
    vi.clearAllMocks();
    vi.unstubAllGlobals();
});

async function openBrowserAndTick() {
    await screen.findByLabelText('Message');
    act(() => useChatStore.getState().setShowFileBrowser(true));
    const row = (await screen.findByText('notes.txt')).closest('.fb-entry') as HTMLElement;
    fireEvent.click(within(row).getByRole('checkbox'));
    return screen.getByRole('button', { name: /Ask Agent/i });
}

const sendsTo = (id: string) => mocked.sendMessageStream.mock.calls.filter((c) => c[0] === id);

describe('Ask Agent from the new-chat screen', () => {
    it('starts one new chat with the file attached and sends the prompt there only', async () => {
        render(<App />);
        fireEvent.click(await openBrowserAndTick());

        await waitFor(() => expect(sendsTo(NEW.id)).toHaveLength(1));
        expect(mocked.createSession).toHaveBeenCalledOnce();
        expect(mocked.createSession.mock.calls[0][0]).toMatchObject({ agent_type: 'gaia', document_ids: ['doc-1'] });
        expect(sendsTo(NEW.id)[0][1]).toBe(PROMPT);
        expect(useChatStore.getState().currentSessionId).toBe(NEW.id);
        expect(useChatStore.getState().showFileBrowser).toBe(false);
        expect(useChatStore.getState().pendingPrompt).toBeNull();
        expect(useChatStore.getState().draftDocumentIds).toEqual([]);

        // Opening another chat afterwards sends nothing there.
        act(() => useChatStore.getState().setCurrentSession(OTHER.id));
        await waitFor(() => expect(mocked.getMessages).toHaveBeenCalledWith(OTHER.id));
        await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
        expect(sendsTo(OTHER.id)).toHaveLength(0);
        expect(mocked.sendMessageStream).toHaveBeenCalledOnce();
    });

    // One act: both clicks land before React re-renders and disables the button.
    it('creates one chat and sends once on a double click', async () => {
        let n = 0;
        mocked.createSession.mockImplementation(async () => (n++ === 0 ? NEW : session({ id: 'sess-new-0002', title: 'New Task' })));
        render(<App />);
        const ask = await openBrowserAndTick();
        act(() => {
            fireEvent.click(ask);
            fireEvent.click(ask);
        });

        await waitFor(() => expect(sendsTo(NEW.id)).toHaveLength(1));
        await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
        expect(mocked.createSession).toHaveBeenCalledOnce();
        expect(mocked.uploadDocumentByPath).toHaveBeenCalledOnce();
        expect(mocked.sendMessageStream).toHaveBeenCalledOnce();
    });

    it('starts the new chat while another chat is still answering, and leaves that one alone', async () => {
        useChatStore.setState({ currentSessionId: OTHER.id });
        render(<App />);
        const box = await screen.findByLabelText('Message');
        await waitFor(() => expect(box).toBeEnabled());
        await userEvent.type(box, 'long question');
        await userEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(sendsTo(OTHER.id)).toHaveLength(1));

        await userEvent.click(screen.getByRole('button', { name: 'New chat' }));
        await waitFor(() => expect(useChatStore.getState().currentSessionId).toBeNull());
        fireEvent.click(await openBrowserAndTick());

        await waitFor(() => expect(sendsTo(NEW.id)).toHaveLength(1));
        expect(sendsTo(NEW.id)[0][1]).toBe(PROMPT);
        expect(sendsTo(OTHER.id)).toHaveLength(1);
        expect(sendsTo(OTHER.id)[0][1]).toBe('long question');
    });

    it('shows the error and sends nothing when the chat cannot be created', async () => {
        mocked.createSession.mockRejectedValue(new Error('backend unreachable'));
        render(<App />);
        fireEvent.click(await openBrowserAndTick());

        expect(await screen.findByText(/backend unreachable/)).toBeInTheDocument();
        expect(useChatStore.getState().showFileBrowser).toBe(true);
        expect(mocked.sendMessageStream).not.toHaveBeenCalled();
        expect(useChatStore.getState().pendingPrompt).toBeNull();
        expect(useChatStore.getState().currentSessionId).toBeNull();
    });

    it('says why nothing was sent when the permission mode could not be set', async () => {
        mocked.setPermissionMode.mockRejectedValue(new Error('backend said no'));
        render(<App />);
        fireEvent.click(await openBrowserAndTick());

        expect(await screen.findByRole('alert')).toHaveTextContent(/permission mode could not be set.*backend said no/);
        expect(mocked.sendMessageStream).not.toHaveBeenCalled();
        expect(useChatStore.getState().pendingPrompt).toBeNull();
    });
});

describe('Ask Agent from an open chat', () => {
    it('sends into that chat without creating another', async () => {
        useChatStore.setState({ currentSessionId: OTHER.id });
        render(<App />);
        fireEvent.click(await openBrowserAndTick());

        await waitFor(() => expect(sendsTo(OTHER.id)).toHaveLength(1));
        expect(sendsTo(OTHER.id)[0][1]).toBe(PROMPT);
        expect(mocked.createSession).not.toHaveBeenCalled();
        expect(mocked.attachDocument).toHaveBeenCalledWith(OTHER.id, 'doc-1');
    });
});
