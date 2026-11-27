// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The session panel in a chat: when it opens on its own, and its "This answer" (#5222, #5279). */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { useSessionPanelStore } from '../../stores/sessionPanelStore';
import type { Session, StreamEvent, TurnSummary } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1',
    title: 'Existing chat',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'gaia',
};

const SUMMARY: TurnSummary = {
    pages: [{ url: 'https://www.amd.com/en', title: 'AMD', kind: 'page' }],
    documents: [],
    files: [],
    sites: ['amd.com'],
    live_data: { attempted: 1, failed: 0 },
    cost: { kind: 'estimate', usd: 0.002 },
    model: { remote: true, provider: 'amd', provider_name: 'AMD LLM Gateway', name: 'm' },
};

let callbacks: api.StreamCallbacks | null = null;

beforeEach(() => {
    vi.clearAllMocks();
    callbacks = null;
    // Any API call the view adds later stays pending instead of returning undefined.
    for (const fn of Object.values(mockedApi)) {
        if (vi.isMockFunction(fn)) fn.mockReturnValue(new Promise(() => {}) as never);
    }
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} } as never);
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.getModelNeed.mockResolvedValue({ job: 'chat', needed: false, model: null, disk_free_gb: null });
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        callbacks = cbs as api.StreamCallbacks;
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    window.localStorage.clear();
    window.localStorage.setItem('gaia.sessionPanel.open', '1');
    useSessionPanelStore.getState().reload();
    useChatStore.setState({
        sessions: [SESSION],
        currentSessionId: SESSION.id,
        messages: [],
        documents: [],
        isStreaming: false,
        streamingContent: '',
        agentSteps: [],
        cards: [],
        isLoadingMessages: false,
        pendingPrompt: null,
        systemStatus: null,
    });
});

function panel() {
    return screen.getByRole('complementary', { name: 'Session panel' });
}

describe('ChatView session panel: this answer', () => {
    it('appears when the turn ends with a summary', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        expect(screen.queryByRole('button', { name: 'This answer' })).not.toBeInTheDocument();
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'what is new at amd' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        await waitFor(() => expect(callbacks).not.toBeNull());
        await act(async () => {
            callbacks!.onDone({ type: 'done', content: 'answer', message_id: 7, turn_summary: SUMMARY } as StreamEvent);
        });
        expect(screen.getByRole('button', { name: 'This answer' })).toHaveAttribute('aria-expanded', 'true');
        expect(panel()).toHaveTextContent('AMD LLM Gateway (cloud)');
        expect(screen.getByRole('link', { name: /AMD/ })).toHaveAttribute('href', 'https://www.amd.com/en');
    });

    it("shows the turn in progress, not the previous answer's data", async () => {
        const finished = {
            messages: [
                { id: 1, session_id: SESSION.id, role: 'user', content: 'first', created_at: '', rag_sources: null },
                { id: 2, session_id: SESSION.id, role: 'assistant', content: 'a', created_at: '', rag_sources: null, turn_summary: SUMMARY },
            ],
            total: 2,
        };
        mockedApi.getMessages.mockResolvedValue(finished as never);
        render(<ChatView sessionId={SESSION.id} />);
        await waitFor(() => expect(
            panel(),
        ).toHaveTextContent('AMD LLM Gateway (cloud)'));
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'second' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        await waitFor(() => expect(
            panel(),
        ).not.toHaveTextContent('AMD LLM Gateway (cloud)'));
        expect(panel()).not.toHaveTextContent('AMD LLM Gateway');
    });

    it('rebuilds the section from a reloaded message', async () => {
        mockedApi.getMessages.mockResolvedValue({
            messages: [
                { id: 1, session_id: SESSION.id, role: 'user', content: 'q', created_at: '', rag_sources: null },
                { id: 2, session_id: SESSION.id, role: 'assistant', content: 'a', created_at: '', rag_sources: null, turn_summary: SUMMARY },
            ],
            total: 2,
        } as never);
        render(<ChatView sessionId={SESSION.id} />);
        await waitFor(() => expect(
            panel(),
        ).toHaveTextContent('amd.com'));
    });
});

const MADE_FILE: TurnSummary = {
    ...SUMMARY,
    files: [{ path: 'C:\\Users\\me\\Documents\\action-items.md', name: 'action-items.md', openable: true }],
};

function narrowWindow(narrow: boolean) {
    Object.defineProperty(window, 'matchMedia', {
        configurable: true,
        value: (query: string) => ({
            matches: narrow && query.includes('max-width'),
            media: query,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }),
    });
}

async function sendAndFinish(summary: TurnSummary) {
    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'save the action items' } });
        fireEvent.click(screen.getByLabelText('Send'));
    });
    await waitFor(() => expect(callbacks).not.toBeNull());
    await act(async () => {
        callbacks!.onDone({ type: 'done', content: 'saved', message_id: 9, turn_summary: summary } as StreamEvent);
    });
}

const noPanel = () => expect(screen.queryByRole('complementary', { name: 'Session panel' })).not.toBeInTheDocument();

describe('ChatView session panel: opens when there is something to show', () => {
    beforeEach(() => {
        window.localStorage.clear();
        useSessionPanelStore.getState().reload();
    });

    afterEach(() => {
        delete (window as { matchMedia?: unknown }).matchMedia;
    });

    it('starts closed for someone who never opened or closed it', () => {
        render(<ChatView sessionId={SESSION.id} />);
        noPanel();
        expect(screen.getByRole('button', { name: 'Show session panel' })).toHaveAttribute('aria-pressed', 'false');
    });

    it("opens beside the chat on the chat's first file, without remembering it or taking focus", async () => {
        render(<ChatView sessionId={SESSION.id} />);
        const composer = screen.getByLabelText('Message');
        composer.focus();
        await sendAndFinish(MADE_FILE);
        expect(panel()).toHaveClass('is-docked');
        expect(window.localStorage.getItem('gaia.sessionPanel.open')).toBeNull();
        expect(panel()).not.toContainElement(document.activeElement as HTMLElement);
    });

    it('stays closed after a turn that made no file', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        await sendAndFinish(SUMMARY);
        noPanel();
    });

    it('stays closed once the user has closed it', async () => {
        window.localStorage.setItem('gaia.sessionPanel.open', '0');
        useSessionPanelStore.getState().reload();
        render(<ChatView sessionId={SESSION.id} />);
        await sendAndFinish(MADE_FILE);
        noPanel();
    });

    it('never opens over the chat on a narrow window', async () => {
        narrowWindow(true);
        render(<ChatView sessionId={SESSION.id} />);
        await sendAndFinish(MADE_FILE);
        expect(screen.queryByRole('dialog', { name: 'Session panel' })).not.toBeInTheDocument();
        expect(useSessionPanelStore.getState().overlayOpen).toBe(false);
        // Nor waits to appear docked once the window widens.
        expect(useSessionPanelStore.getState().open).toBe(false);
    });

    it('stays closed on opening a chat that already made files, and when it makes more', async () => {
        mockedApi.getMessages.mockResolvedValue({
            messages: [
                { id: 1, session_id: SESSION.id, role: 'user', content: 'earlier question', created_at: '', rag_sources: null },
                { id: 2, session_id: SESSION.id, role: 'assistant', content: 'earlier answer', created_at: '', rag_sources: null, turn_summary: MADE_FILE },
            ],
            total: 2,
        } as never);
        render(<ChatView sessionId={SESSION.id} />);
        await screen.findByText('earlier answer');
        noPanel();
        await sendAndFinish(MADE_FILE);
        noPanel();
    });
});
