// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A message sent in one chat never shows up in another chat opened while its model check ran (#7591). */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Message, Session, StreamEvent } from '../../types';
import * as api from '../../services/api';
import { resetQueueDispatch, startQueueDispatcher } from '../../services/queueDispatch';
import { chatDraftKey, readSavedDraft } from '../../utils/savedDraft';

vi.mock('../../services/api');

const modelCheck = vi.hoisted(() => ({ resolve: null as ((ok: boolean) => void) | null }));
vi.mock('../../stores/modelNeededStore', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../stores/modelNeededStore')>()),
    modelKnownReady: () => false,
    ensureModel: vi.fn(() => new Promise<boolean>((resolve) => { modelCheck.resolve = resolve; })),
}));

const mockedApi = vi.mocked(api);

const A = 'chat-a';
const B = 'chat-b';
const QUESTION = 'question asked in chat A';

const session = (id: string): Session => ({
    id,
    title: `Chat ${id}`,
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 1,
    document_ids: [],
    agent_type: 'gaia',
});

const saved: Record<string, Message[]> = {
    [A]: [],
    [B]: [{ id: 1, session_id: B, role: 'user', content: 'earlier message in chat B', created_at: '2026-06-10T00:00:00Z', rag_sources: null }],
};

let sends: { sessionId: string; text: string; cbs: api.StreamCallbacks }[] = [];
let running: string[] = [];
let stopDispatcher: (() => void) | null = null;

beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sends = [];
    running = [];
    modelCheck.resolve = null;
    resetQueueDispatch();
    mockedApi.getMessages.mockImplementation(async (sid: string) => ({ messages: saved[sid], total: saved[sid].length }));
    mockedApi.getMessageCount.mockImplementation(async (sid: string) => saved[sid].length);
    mockedApi.getActiveRuns.mockImplementation(async () => ({ session_ids: running, queued: [], awaiting_user: {} }));
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.sendMessageStream.mockImplementation((sid, text, cbs) => {
        sends.push({ sessionId: sid, text, cbs: cbs as api.StreamCallbacks });
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        sessions: [session(A), session(B)],
        currentSessionId: A,
        messages: [],
        documents: [],
        isStreaming: false,
        streamingContent: '',
        agentSteps: [],
        cards: [],
        isLoadingMessages: false,
        pendingPrompt: null,
        systemStatus: null,
        messageQueues: {},
        queueHolds: {},
        runningSessionIds: [],
        queuedSessionIds: [],
        activeRunsAt: 0,
    });
});

afterEach(() => {
    stopDispatcher?.();
    stopDispatcher = null;
});

/** Open chat A and send a question; the model check is still running when this returns. */
async function sendInA() {
    const view = render(<ChatView key={A} sessionId={A} />);
    await act(async () => {});
    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: QUESTION } });
        fireEvent.keyDown(screen.getByLabelText('Message'), { key: 'Enter' });
    });
    expect(modelCheck.resolve).not.toBeNull();
    return view;
}

const finishModelCheck = () => act(async () => { modelCheck.resolve!(true); });

/** The sidebar picks chat B: the store flips first, the shown view follows. */
const pickB = () => act(async () => { useChatStore.setState({ currentSessionId: B }); });

function expectBShowsOnlyItsOwn() {
    expect(screen.getByText('earlier message in chat B')).toBeTruthy();
    expect(screen.queryByText(QUESTION)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull();
    expect(screen.queryByPlaceholderText('Queue your next message')).toBeNull();
    expect(useChatStore.getState().messages.some((m) => m.content === QUESTION)).toBe(false);
    expect(useChatStore.getState().isStreaming).toBe(false);
    expect(sends.filter((s) => s.sessionId === B)).toEqual([]);
}

describe('opening another chat while a send waits on the model check', () => {
    it('keeps the question out of chat B when the check ends after B is shown', async () => {
        const view = await sendInA();
        await pickB();
        view.unmount();
        render(<ChatView key={B} sessionId={B} />);
        await act(async () => {});

        await finishModelCheck();
        expectBShowsOnlyItsOwn();
    });

    it('keeps the question out of chat B when the check ends before B is shown', async () => {
        const view = await sendInA();
        await pickB();
        await finishModelCheck();
        view.unmount();
        render(<ChatView key={B} sessionId={B} />);
        await act(async () => {});

        expectBShowsOnlyItsOwn();
    });

    it('sends the question to chat A instead, and clears its saved draft', async () => {
        const view = await sendInA();
        await pickB();
        view.unmount();
        render(<ChatView key={B} sessionId={B} />);
        await act(async () => {});
        await finishModelCheck();

        expect(readSavedDraft(chatDraftKey(A))).toBe('');
        stopDispatcher = startQueueDispatcher();
        await act(async () => {
            useChatStore.getState().setActiveRuns({ session_ids: [], queued: [], awaiting_user: {} }, Date.now() + 1_000);
        });
        expect(sends.map((s) => [s.sessionId, s.text])).toEqual([[A, QUESTION]]);
        await act(async () => { sends[0].cbs.onAgentEvent({ type: 'status', phase: 'working' } as StreamEvent); });
        expectBShowsOnlyItsOwn();
    });
});
