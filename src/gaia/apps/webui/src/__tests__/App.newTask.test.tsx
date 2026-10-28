// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Pins what the shell sends when a chat starts, when it shows first-run setup,
// and which sidebar destination is open.

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { useModelStore } from '../stores/modelStore';
import type { Session, SetupCheck, StartCard } from '../types';

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
    mocked.createSession.mockResolvedValue(session({ id: 'sess-new-0001', title: 'New Task', message_count: 0, agent_type: 'gaia' }));
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

    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    useChatStore.setState({
        sessions: [], currentSessionId: null, messages: [], agents: [], pendingPrompt: null,
        draftPermissionMode: 'ask', defaultPermissionMode: 'ask', showMemoryDashboard: false, showSchedules: false, settingsSection: null,
        draftDocumentIds: [], draftPrivate: false,
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

    it('starts with the documents indexed while no chat was open, then forgets them', async () => {
        useChatStore.setState({ draftDocumentIds: ['doc-lark-1'] });
        render(<App />);
        await sendFirstMessage('what is the dock code?');

        await waitFor(() => expect(mocked.createSession).toHaveBeenCalledOnce());
        expect(mocked.createSession.mock.calls[0][0]).toMatchObject({ document_ids: ['doc-lark-1'] });
        expect(useChatStore.getState().draftDocumentIds).toEqual([]);
    });

    it('starts with the folder picked on the new-chat page, then forgets it', async () => {
        useChatStore.setState({ draftFolder: '/home/u/site' });
        render(<App />);
        await sendFirstMessage('list the pages');

        await waitFor(() => expect(mocked.createSession).toHaveBeenCalledOnce());
        expect(mocked.createSession.mock.calls[0][0]).toMatchObject({ folders: ['/home/u/site'] });
        expect(useChatStore.getState().draftFolder).toBeNull();
    });

    it('sends "ask" too, so a full-access default in config cannot hide behind the chip', async () => {
        render(<App />);
        await sendFirstMessage('go');

        await waitFor(() => expect(mocked.setPermissionMode).toHaveBeenCalledWith('sess-new-0001', 'ask'));
    });

    it('starts in full access when config makes that the default, and returns to it after', async () => {
        mocked.listAllPermissions.mockResolvedValue({ default_mode: 'full_access', sessions: [] });
        render(<App />);
        await waitFor(() => expect(useChatStore.getState().draftPermissionMode).toBe('full_access'));
        expect(await screen.findByRole('button', { name: 'Permission mode: full access' })).toBeInTheDocument();
        await sendFirstMessage('go');

        await waitFor(() => expect(mocked.setPermissionMode).toHaveBeenCalledWith('sess-new-0001', 'full_access'));
        expect(useChatStore.getState().draftPermissionMode).toBe('full_access');
    });

    it('does not send the message when the permission mode could not be set', async () => {
        mocked.setPermissionMode.mockRejectedValue(new Error('backend said no'));
        render(<App />);
        await sendFirstMessage('go');

        expect(await screen.findByRole('alert')).toHaveTextContent(/permission mode could not be set.*backend said no/);
        expect(mocked.sendMessageStream).not.toHaveBeenCalled();
        // The unsent message waits in the new chat's own box, not back in New Chat.
        expect(await screen.findByLabelText('Message')).toHaveValue('go');
        expect(localStorage.getItem('gaia:draft:new-chat')).toBeNull();
    });

    it('forgets the first message once sent, so New Chat does not offer it again', async () => {
        render(<App />);
        await sendFirstMessage('summarise my notes');
        await waitFor(() => expect(mocked.sendMessageStream).toHaveBeenCalled());
        expect(localStorage.getItem('gaia:draft:new-chat')).toBeNull();
    });
});

describe('a new chat sent several times before it opens', () => {
    async function typeFirstMessage(text: string) {
        const box = await screen.findByLabelText('Message');
        await waitFor(() => expect(box).toBeEnabled());
        await userEvent.type(box, text);
        return box;
    }

    // One act: all three presses land before React re-renders, like a bouncy key.
    it('creates one chat and sends the message once on three quick Enters', async () => {
        render(<App />);
        const box = await typeFirstMessage('hello there');
        act(() => {
            for (let i = 0; i < 3; i++) fireEvent.keyDown(box, { key: 'Enter' });
        });

        await waitFor(() => expect(mocked.sendMessageStream).toHaveBeenCalled());
        expect(mocked.createSession).toHaveBeenCalledOnce();
        expect(mocked.sendMessageStream).toHaveBeenCalledOnce();
        expect(useChatStore.getState().sessions).toHaveLength(1);
    });

    it('creates one chat and sends the message once on three quick Send clicks', async () => {
        render(<App />);
        await typeFirstMessage('hello there');
        const send = screen.getByRole('button', { name: 'Send' });
        act(() => {
            for (let i = 0; i < 3; i++) fireEvent.click(send);
        });

        await waitFor(() => expect(mocked.sendMessageStream).toHaveBeenCalled());
        expect(mocked.createSession).toHaveBeenCalledOnce();
        expect(mocked.sendMessageStream).toHaveBeenCalledOnce();
        expect(useChatStore.getState().sessions).toHaveLength(1);
    });

    it('lets the message be sent again, still typed, when the chat could not be created', async () => {
        mocked.createSession.mockRejectedValueOnce(new Error('backend unreachable'));
        render(<App />);
        const box = await typeFirstMessage('hello there');
        fireEvent.keyDown(box, { key: 'Enter' });

        expect(await screen.findByRole('alert')).toHaveTextContent('backend unreachable');
        expect(box).toHaveValue('hello there');
        expect(box).toBeEnabled();
        const send = screen.getByRole('button', { name: 'Send' });
        expect(send).toBeEnabled();

        await userEvent.click(send);
        await waitFor(() => expect(mocked.sendMessageStream).toHaveBeenCalledOnce());
        expect(mocked.createSession).toHaveBeenCalledTimes(2);
        expect(mocked.sendMessageStream.mock.calls[0].slice(0, 2)).toEqual(['sess-new-0001', 'hello there']);
    });
});

describe('a private new chat', () => {
    it('is created private before its first message goes out', async () => {
        mocked.createSession.mockImplementation(async (data) => session({
            id: 'sess-new-0001', title: 'New Task', message_count: 0, agent_type: 'gaia', private: !!data?.private,
        }));
        render(<App />);
        const chip = await screen.findByRole('button', { name: 'Private chat' });
        expect(chip).toHaveAttribute('aria-pressed', 'false');
        await userEvent.click(chip);
        expect(chip).toHaveAttribute('aria-pressed', 'true');
        await sendFirstMessage('Please remember: my bank PIN hint is the dog');

        await waitFor(() => expect(mocked.sendMessageStream).toHaveBeenCalled());
        expect(mocked.createSession).toHaveBeenCalledOnce();
        expect(mocked.createSession.mock.calls[0][0]).toMatchObject({ private: true });
        // The first message goes to the chat that already exists as private.
        expect(mocked.sendMessageStream.mock.calls[0][0]).toBe('sess-new-0001');
        expect(mocked.createSession.mock.invocationCallOrder[0])
            .toBeLessThan(mocked.sendMessageStream.mock.invocationCallOrder[0]);
        expect(mocked.toggleSessionPrivacy).not.toHaveBeenCalled();
        expect(useChatStore.getState().sessions.find((s) => s.id === 'sess-new-0001')?.private).toBe(true);
        expect(await screen.findByRole('button', { name: 'Turn off private chat' })).toHaveAttribute('data-private', 'true');
        // The next new chat starts non-private again.
        expect(useChatStore.getState().draftPrivate).toBe(false);
    });

    it('is not private unless the toggle is on', async () => {
        render(<App />);
        await sendFirstMessage('hello');

        await waitFor(() => expect(mocked.createSession).toHaveBeenCalledOnce());
        expect(mocked.createSession.mock.calls[0][0]).toMatchObject({ private: false });
    });
});

describe('first-run setup', () => {
    it('a cold start shows a neutral loading screen until the setup check answers, never the chat', async () => {
        let answer!: (c: SetupCheck) => void;
        mocked.checkSetup.mockImplementation(() => new Promise((r) => { answer = r; }));
        mocked.getSystemStatus.mockResolvedValue({ lemonade_running: false, model_loaded: null, initialized: false } as never);
        render(<App />);

        expect(await screen.findByText('Starting GAIA…')).toBeInTheDocument();
        await waitFor(() => expect(mocked.getSystemStatus).toHaveBeenCalled());
        expect(screen.queryByLabelText('Message')).not.toBeInTheDocument();

        answer(NOT_READY);
        expect(await screen.findByRole('heading', { name: 'Set up GAIA' })).toBeInTheDocument();
        expect(screen.queryByLabelText('Message')).not.toBeInTheDocument();
    });

    it('a PC set up before goes straight to chat while the check runs', async () => {
        mocked.checkSetup.mockImplementation(() => new Promise(() => { /* still checking */ }));
        mocked.getSystemStatus.mockResolvedValue({ lemonade_running: true, model_loaded: null, initialized: true } as never);
        render(<App />);
        expect(await screen.findByLabelText('Message')).toBeInTheDocument();
        expect(screen.queryByText('Starting GAIA…')).not.toBeInTheDocument();
    });

    async function finishSetupWith(card: StartCard) {
        mocked.checkSetup.mockResolvedValue(NOT_READY);
        mocked.startSetup.mockResolvedValue({ state: 'ready', steps: [] });
        mocked.getStartScreen.mockResolvedValue({ cards: [card], working_folder: { path: '/w', name: 'w', gaia_folder: false, choosable: true } });
        render(<App />);
        await userEvent.click(await screen.findByRole('button', { name: 'Start setup' }));
        await userEvent.click(await screen.findByRole('button', { name: new RegExp(card.title) }));
    }

    it('a first task picked at the end of setup starts the first chat', async () => {
        await finishSetupWith({ skill: 'check-in', title: 'Check in', note: '', prompt: 'Good morning. Let us check in.', needs_input: false });
        await waitFor(() => expect(mocked.createSession).toHaveBeenCalledOnce());
        await waitFor(() => expect(mocked.sendMessageStream).toHaveBeenCalled());
        expect(mocked.sendMessageStream.mock.calls[0][1]).toBe('Good morning. Let us check in.');
    });

    it('a first task that needs a subject opens a new chat with it typed in', async () => {
        await finishSetupWith({ skill: 'summarize', title: 'Summarize something', note: '', prompt: 'Summarize this: ', needs_input: true });
        expect(await screen.findByLabelText('Message')).toHaveValue('Summarize this: ');
        expect(mocked.createSession).not.toHaveBeenCalled();
    });

    it('re-reads the agents when setup finishes, so the agent it installed counts', async () => {
        mocked.checkSetup.mockResolvedValue(NOT_READY);
        mocked.startSetup.mockResolvedValue({ state: 'ready', steps: [] });
        render(<App />);
        await waitFor(() => expect(mocked.listAgents).toHaveBeenCalled());
        mocked.listAgents.mockResolvedValue({
            agents: [{ id: 'gaia', name: 'GAIA', namespaced_agent_id: 'installed:gaia' }] as never, total: 1,
        });

        await userEvent.click(await screen.findByRole('button', { name: 'Start setup' }));
        await userEvent.click(await screen.findByRole('button', { name: 'Start chatting' }));

        expect(await screen.findByLabelText('Message')).toBeInTheDocument();
        await waitFor(() =>
            expect(screen.queryByText(/The GAIA agent isn.t installed/)).not.toBeInTheDocument(),
        );
    });

    it('shows the setup screen when GAIA is not set up', async () => {
        mocked.checkSetup.mockResolvedValue(NOT_READY);
        render(<App />);

        expect(await screen.findByRole('heading', { name: 'Set up GAIA' })).toBeInTheDocument();
        // Setup reads as progress: the check's reason is not shown as a problem.
        expect(screen.getByText('A few minutes of setup, then your first chat.')).toBeInTheDocument();
        expect(screen.queryByText('The local model server is not installed.')).not.toBeInTheDocument();
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

// A chat's link (#<short hash>) reloaded to the home page: with no session
// selected yet, the hash-sync effect cleared the hash before the deferred URL
// handler read it.
describe('opening the app on a chat link', () => {
    afterEach(() => {
        window.history.replaceState(null, '', '/');
        useChatStore.setState({ sessions: [], currentSessionId: null });
    });

    it('opens that chat and keeps its link', async () => {
        const chat = session({ id: '2676152b-4c2d-4b6e-af47-c790ffd3f2b7' });
        mocked.listSessions.mockResolvedValue({ sessions: [chat], total: 1 });
        useChatStore.setState({ sessions: [], currentSessionId: null });
        window.history.replaceState(null, '', '/#2676152');

        render(<App />);

        await waitFor(() =>
            expect(useChatStore.getState().currentSessionId).toBe(chat.id),
        );
        expect(window.location.hash).toBe('#2676152');
    });

    it('drops a link to a chat that no longer exists', async () => {
        mocked.listSessions.mockResolvedValue({ sessions: [session()], total: 1 });
        useChatStore.setState({ sessions: [], currentSessionId: null });
        window.history.replaceState(null, '', '/#deadbee');

        render(<App />);

        await waitFor(() => expect(mocked.listSessions).toHaveBeenCalled());
        await waitFor(() => expect(window.location.hash).toBe(''));
        expect(useChatStore.getState().currentSessionId).toBeNull();
    });
});

describe('sidebar destinations', () => {
    it('opens Scheduled tasks over the chat, and Documents in its place', async () => {
        mocked.listSchedules.mockResolvedValue({ schedules: [], total: 0 });
        render(<App />);
        await userEvent.click(await screen.findByRole('button', { name: 'Scheduled tasks' }));

        expect(await screen.findByRole('dialog', { name: 'Scheduled tasks' })).toBeInTheDocument();
        expect(screen.getByLabelText('Message')).toBeInTheDocument();

        // The backdrop covers the sidebar for a pointer, not for a screen reader or voice control.
        fireEvent.click(screen.getByRole('button', { name: 'Documents' }));

        expect(await screen.findByRole('dialog', { name: 'Document library' })).toBeInTheDocument();
        expect(screen.queryByRole('dialog', { name: 'Scheduled tasks' })).not.toBeInTheDocument();
    });
});
