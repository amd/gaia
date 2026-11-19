// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A new chat's first message waits as pendingPrompt until the chat has loaded,
 * then goes out on the next tick. New chat clicked in that tick must still
 * find the message to hand back, or it is cleaned up with the empty chat.
 */

import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Session } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'sess-new',
    title: 'New Task',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'gemma',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'gaia',
};

const TEXT = 'Reply with exactly: HANDOFF-1';

beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 } as never);
    mockedApi.getMessageCount.mockResolvedValue(0 as never);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: SESSION.id, mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.sendMessageStream.mockImplementation(() => new AbortController());
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        sessions: [SESSION], currentSessionId: SESSION.id, messages: [], documents: [],
        isStreaming: false, streamingContent: '', agentSteps: [], cards: [], isLoadingMessages: false,
        pendingPrompt: { sessionId: SESSION.id, text: TEXT }, systemStatus: null, messageQueues: {}, pendingDeleteIds: [],
    });
});

afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
});

/** Render and let the history load, without running the send's timer. */
async function openLoaded() {
    const view = render(<ChatView sessionId={SESSION.id} />);
    for (let i = 0; i < 5; i++) await act(async () => { await Promise.resolve(); });
    expect(useChatStore.getState().isLoadingMessages).toBe(false);
    return view;
}

describe("a new chat's first message", () => {
    it('can still be taken back by New chat in the tick before it is sent', async () => {
        const view = await openLoaded();

        // What New chat does with an unsent first message.
        const taken = useChatStore.getState().pendingPrompt;
        expect(taken?.text).toBe(TEXT);
        useChatStore.getState().setPendingPrompt(null);
        view.unmount();
        await act(async () => { await vi.advanceTimersByTimeAsync(10); });

        expect(mockedApi.sendMessageStream).not.toHaveBeenCalled();
    });

    it('waits for its chat when the user leaves for another one in that tick', async () => {
        const view = await openLoaded();
        view.unmount();
        await act(async () => { await vi.advanceTimersByTimeAsync(10); });

        expect(mockedApi.sendMessageStream).not.toHaveBeenCalled();
        expect(useChatStore.getState().pendingPrompt).toEqual({ sessionId: SESSION.id, text: TEXT });
    });

    it('goes out once, and is then forgotten', async () => {
        await openLoaded();
        await act(async () => { await vi.advanceTimersByTimeAsync(10); });

        expect(mockedApi.sendMessageStream).toHaveBeenCalledTimes(1);
        expect(mockedApi.sendMessageStream.mock.calls[0][1]).toBe(TEXT);
        expect(useChatStore.getState().pendingPrompt).toBeNull();
    });
});
