// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The first send after a reload waits on a model check before it shows the
 * question. Switching chats during that wait must not draw the question,
 * "Thinking…" and Stop into the chat the user moved to (#7591).
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Session } from '../../types';
import * as api from '../../services/api';
import * as modelNeeded from '../../stores/modelNeededStore';

vi.mock('../../services/api');
vi.mock('../../stores/modelNeededStore', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../stores/modelNeededStore')>()),
    modelKnownReady: vi.fn(() => false),
    ensureModel: vi.fn(),
}));

const mockedApi = vi.mocked(api);
const mockedEnsureModel = vi.mocked(modelNeeded.ensureModel);

const session = (id: string): Session => ({
    id, title: id, created_at: '2026-11-12T00:00:00Z', updated_at: '2026-11-12T00:00:00Z',
    model: 'qwen', system_prompt: null, message_count: 0, document_ids: [], agent_type: 'gaia',
});
const A = session('chat-a');
const B = session('chat-b');
const QUESTION = 'Summarize the canal report.';

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getMessageCount.mockResolvedValue(0);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: A.id, mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.sendMessageStream.mockImplementation(() => new AbortController());
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        sessions: [A, B], currentSessionId: A.id, messages: [], documents: [],
        isStreaming: false, streamingContent: '', agentSteps: [], cards: [], isLoadingMessages: false,
        pendingPrompt: null, systemStatus: null, messageQueues: {},
    });
});

describe('switching chats while the first send checks the model', () => {
    it('leaves the other chat untouched and sends nothing', async () => {
        let resolveCheck: (ready: boolean) => void = () => {};
        mockedEnsureModel.mockImplementation(() => new Promise<boolean>((r) => { resolveCheck = r; }));

        render(<ChatView sessionId={A.id} />);
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: QUESTION } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        expect(mockedEnsureModel).toHaveBeenCalledWith('chat');

        // The user opens chat B before the check returns.
        act(() => { useChatStore.setState({ currentSessionId: B.id }); });
        await act(async () => { resolveCheck(true); });

        const state = useChatStore.getState();
        expect(state.messages).toEqual([]);
        expect(state.isStreaming).toBe(false);
        expect(mockedApi.sendMessageStream).not.toHaveBeenCalled();
        // The question stays in chat A's box as an unsent draft.
        expect(screen.getByLabelText('Message')).toHaveValue(QUESTION);
    });

    it('still sends when the user stays in the chat', async () => {
        mockedEnsureModel.mockResolvedValue(true);

        render(<ChatView sessionId={A.id} />);
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: QUESTION } });
            fireEvent.click(screen.getByLabelText('Send'));
        });

        expect(mockedApi.sendMessageStream).toHaveBeenCalledTimes(1);
        expect(useChatStore.getState().messages.map((m) => m.content)).toEqual([QUESTION]);
    });
});
