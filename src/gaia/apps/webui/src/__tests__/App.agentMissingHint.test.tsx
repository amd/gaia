// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A first message refused because the GAIA agent isn't installed, from each way
// of starting a chat other than the New chat box: it waits in that box under the
// translated "install it from the notice above" line, which leaves with the notice (#8247).

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');
vi.mock('../services/memoryApi');

import App from '../App';
import * as api from '../services/api';
import * as memoryApi from '../services/memoryApi';
import { useChatStore } from '../stores/chatStore';
import { useModelStore } from '../stores/modelStore';
import { translator } from '../i18n/useT';
import { requestPersonalize } from '../utils/connectorPersonalize';
import { FLAGSHIP_AGENT_ID } from '../utils/newTask';
import type { AgentInfo, BrowseResponse, SetupCheck } from '../types';

const mocked = vi.mocked(api);
const OLD_ENGLISH = translator('en')('agentNotice.sendRefused');
const LANGS = ['en', 'ar'] as const;
type Lang = typeof LANGS[number];

/** The innermost element reading exactly *text*; Arabic adds its English in brackets, in an element of its own. */
const whole = (text: string) => (_: string, el: Element | null) =>
    el?.textContent === text && ![...el.children].some((c) => c.textContent === text);

const GAIA: AgentInfo = {
    id: FLAGSHIP_AGENT_ID, name: 'GAIA', description: '', source: 'installed', conversation_starters: [], models: [],
} as unknown as AgentInfo;

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

const NOT_READY: SetupCheck = {
    ready: false,
    stage: 'setup',
    reasons: ['The local model server is not installed'],
    steps: [
        { key: 'server', label: 'Install the model server', detail: '' },
        { key: 'models', label: 'Download models', detail: '' },
    ],
};

/** The backend's answer to a new chat when the flagship isn't installed (sessions.py `_reject_unknown_agent_type`). */
const refused = () => Object.assign(new Error(`Unknown agent_type '${FLAGSHIP_AGENT_ID}'`), {
    httpStatus: 422, detail: `Unknown agent_type '${FLAGSHIP_AGENT_ID}'`,
});

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
    mocked.createSession.mockRejectedValue(refused());
    mocked.getMessages.mockResolvedValue({ messages: [] } as never);
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
    mocked.listDocuments.mockResolvedValue({ documents: [DOC] } as never);
    mocked.getMessageCount.mockResolvedValue({ count: 0 } as never);
    mocked.browseFiles.mockResolvedValue(BROWSE);
    mocked.uploadDocumentByPath.mockResolvedValue(DOC as never);
    vi.mocked(memoryApi.getMemorySettings).mockResolvedValue({
        memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false,
    });

    localStorage.clear();
    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    useChatStore.setState({
        sessions: [], currentSessionId: null, messages: [], agents: [], pendingPrompt: null,
        draftPermissionMode: 'ask', defaultPermissionMode: 'ask', showMemoryDashboard: false, showSchedules: false,
        settingsSection: null, draftDocumentIds: [], draftPrivate: false, showFileBrowser: false, locale: 'en',
    });
});

afterEach(() => {
    window.history.replaceState(null, '', '/');
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    localStorage.clear();
});

/** The hint is translated, the refused message waits in the box, and both the hint and the notice leave once the agent is in. */
async function expectHintUntilInstalled(lang: Lang, sent: string) {
    const m = translator(lang);
    const hint = await screen.findByText(whole(m('agentNotice.sendRefused')));
    expect(hint.closest('.new-chat-error[role="alert"]')).not.toBeNull();
    if (lang !== 'en') expect(screen.queryByText(whole(OLD_ENGLISH))).toBeNull();
    expect(screen.getByLabelText(m('composer.message'))).toHaveValue(sent);
    expect(mocked.sendMessageStream).not.toHaveBeenCalled();

    // Installed from the CLI meanwhile: the window regaining focus re-reads the agents.
    mocked.listAgents.mockResolvedValue({ agents: [GAIA], total: 1 });
    act(() => { window.dispatchEvent(new Event('focus')); });
    await waitFor(() => expect(screen.queryByText(whole(m('agentNotice.missing')))).toBeNull());
    expect(screen.queryByText(whole(m('agentNotice.sendRefused')))).toBeNull();
}

describe.each(LANGS)('a first message refused for want of the GAIA agent (%s)', (lang) => {
    const m = translator(lang);

    it('from Personalize waits in the New chat box under the hint', async () => {
        useChatStore.setState({ locale: lang });
        render(<App />);
        await screen.findByLabelText(m('composer.message'));
        await screen.findByText(whole(m('agentNotice.missing')));

        act(() => requestPersonalize({ connectorId: 'google', displayName: 'Google' }));

        await waitFor(() => expect(mocked.createSession).toHaveBeenCalledOnce());
        const box = await screen.findByLabelText(m('composer.message'));
        await waitFor(() => expect((box as HTMLTextAreaElement).value).toContain('I just connected Google'));
        await expectHintUntilInstalled(lang, (box as HTMLTextAreaElement).value);
    });

    it('from the first card after setup waits in the New chat box under the hint', async () => {
        useChatStore.setState({ locale: lang });
        mocked.checkSetup.mockResolvedValue(NOT_READY);
        mocked.startSetup.mockResolvedValue({ state: 'ready', steps: [] });
        mocked.getStartScreen.mockResolvedValue({
            cards: [{ skill: 'coding', title: 'Work on code', note: '', prompt: 'Tell me what this project does.', needs_input: false }],
            working_folder: { path: '/w', name: 'w', gaia_folder: false, choosable: true },
        });
        render(<App />);
        await userEvent.click(await screen.findByRole('button', { name: m('setup.models.useRecommended') }));
        await userEvent.click(await screen.findByRole('button', { name: /Work on code/ }));

        await waitFor(() => expect(mocked.createSession).toHaveBeenCalledOnce());
        await expectHintUntilInstalled(lang, 'Tell me what this project does.');
    });

    it("from the file browser's Ask GAIA waits in the New chat box under the hint", async () => {
        useChatStore.setState({ locale: lang });
        render(<App />);
        await screen.findByLabelText(m('composer.message'));
        act(() => useChatStore.getState().setShowFileBrowser(true));
        const row = (await screen.findByText('notes.txt')).closest('.fb-entry') as HTMLElement;
        fireEvent.click(within(row).getByRole('checkbox'));
        fireEvent.click(screen.getByRole('button', { name: m('fileBrowser.askGaia') }));

        await waitFor(() => expect(mocked.createSession).toHaveBeenCalledOnce());
        await waitFor(() => expect(useChatStore.getState().showFileBrowser).toBe(false));
        expect(useChatStore.getState().draftDocumentIds).toEqual(['doc-1']);
        await expectHintUntilInstalled(lang, 'Please analyze this document for me: notes.txt');
    });
});
