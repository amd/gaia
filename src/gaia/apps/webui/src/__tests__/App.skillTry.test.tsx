// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** "Try in a new chat" on a skill puts a prompt in the composer; it never sends on its own (#7409). */

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/api');
// Only rendered when setup is needed; stands in for the real screen's "Start chatting".
vi.mock('../components/SetupScreen', () => ({
    SetupScreen: ({ onReady }: { onReady: () => void }) => (
        <button type="button" onClick={() => onReady()}>Start chatting</button>
    ),
}));

import App from '../App';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { resetModelRetryForTests, useModelStore } from '../stores/modelStore';
import type { AgentInfo, Session, SetupCheck } from '../types';

const mocked = vi.mocked(api);

const READY: SetupCheck = { ready: true, steps: [] };
const GAIA_AGENT: AgentInfo = {
    id: 'gaia', name: 'GAIA', description: '', source: 'installed', conversation_starters: [], models: [],
};
const NOT_LISTENING = () => new TypeError('Failed to fetch');

function session(over: Partial<Session> = {}): Session {
    return {
        id: 'sess-new-0001',
        title: 'New Task',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
        model: '',
        system_prompt: null,
        message_count: 0,
        document_ids: [],
        agent_type: 'gaia',
        ...over,
    };
}

beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    vi.stubGlobal('EventSource', class {
        close() { /* noop */ }
        addEventListener() { /* noop */ }
    });

    mocked.getActiveModel.mockResolvedValue({
        provider: 'local', model: 'Qwen3.6-35B-A3B-GGUF', label: 'Qwen', display_name: 'Qwen 3.6', remote: false, is_default: true,
    } as never);
    mocked.listProviderModels.mockResolvedValue({ models: [] } as never);
    mocked.checkSetup.mockResolvedValue(READY);
    mocked.requestWarmUp.mockResolvedValue({ state: 'ready' });
    mocked.getStartScreen.mockResolvedValue({ cards: [], working_folder: { path: '/w', name: 'w', gaia_folder: false, choosable: true } });
    mocked.getSystemStatus.mockResolvedValue({ lemonade_running: true, model_loaded: 'x', initialized: true } as never);
    mocked.listAgents.mockReset().mockResolvedValue({ agents: [GAIA_AGENT], total: 1 });
    mocked.listSessions.mockResolvedValue({ sessions: [], total: 0 });
    mocked.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mocked.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mocked.getTunnelStatus.mockResolvedValue({ active: false } as never);
    mocked.createSession.mockResolvedValue(session());
    mocked.getMessages.mockResolvedValue({ messages: [] } as never);
    mocked.getPermissions.mockResolvedValue({ session_id: 'sess-new-0001', mode: 'full_access', grants: [] });
    mocked.setPermissionMode.mockImplementation(async (sid, mode) => ({ session_id: sid, mode, grants: [] }));
    mocked.listAllPermissions.mockResolvedValue({ default_mode: 'full_access', sessions: [] });
    mocked.sendMessageStream.mockImplementation(() => new AbortController());
    mocked.listDocuments.mockResolvedValue({ documents: [] } as never);
    mocked.getMessageCount.mockResolvedValue({ count: 0 } as never);

    resetModelRetryForTests();
    useModelStore.setState({ active: null, error: null, restoreNotice: null, names: {} });
    useChatStore.setState({
        sessions: [], currentSessionId: null, messages: [], agents: [], pendingPrompt: null,
        draftPermissionMode: 'ask', draftPermissionModeChosen: false,
        defaultPermissionMode: 'ask', defaultPermissionModeKnown: false,
        showMemoryDashboard: false, showSchedules: false, settingsSection: null, draftDocumentIds: [],
    });
});

afterEach(() => {
    resetModelRetryForTests();
    vi.useRealTimers();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
});

async function advance(ms: number) {
    await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

describe('trying a skill from Settings', () => {
    it('opens a new chat with the prompt in the box, and sends nothing', async () => {
        mocked.listSkills.mockResolvedValue({
            skills: [{ name: 'pdf-forms', description: 'Fill PDF forms', version: null, tier: null, tools: [], path: '/s/pdf-forms/SKILL.md', source: 'agent-bundled' }],
            invalid: {},
        });
        mocked.listFeaturedSkillLibraries.mockResolvedValue({ libraries: [] });
        render(<App />);
        await advance(0);
        act(() => useChatStore.getState().openSettings('skills'));
        await advance(0);
        const row = screen.getByText('pdf-forms').closest('li')!;
        fireEvent.click(within(row).getByRole('button', { name: /Try .* in a new chat/ }));
        await advance(0);

        expect(useChatStore.getState().settingsSection).toBeNull();
        const box = screen.getByLabelText('Message') as HTMLTextAreaElement;
        expect(box.value).toBe('Load the pdf-forms skill, then tell me briefly what it lets you do.');
        expect(mocked.createSession).not.toHaveBeenCalled();
        expect(mocked.sendMessageStream).not.toHaveBeenCalled();
    });
});
