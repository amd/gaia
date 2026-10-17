// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** "Retry with…" shows both answers labelled by model; keeping one continues the chat from it. */

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Message, RetryResult, Session } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);
// A variable, not a literal: compiles before and after #5242 adds `queued` and `awaiting_user`.
const NO_ACTIVE_RUNS = { session_ids: [], queued: [], awaiting_user: {} };

const SESSION: Session = {
    id: 'session-r',
    title: 'Retry chat',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'Gemma-4-E4B-it-GGUF',
    effective_model: 'Gemma-4-E4B-it-GGUF',
    system_prompt: null,
    message_count: 2,
    document_ids: [],
    agent_type: 'gaia',
} as Session;

const MESSAGES: Message[] = [
    { id: 1, session_id: SESSION.id, role: 'user', content: 'Name a prime', created_at: '2026-06-10T00:00:00Z', rag_sources: null },
    {
        id: 2, session_id: SESSION.id, role: 'assistant', content: 'Seven.', created_at: '2026-06-10T00:00:04Z', rag_sources: null,
        stats: { tokens_per_second: 31, time_to_first_token: 0.2, input_tokens: 900, output_tokens: 3 },
    },
];

const RESULT: RetryResult = {
    message_id: 2,
    model: 'Qwen3.6-35B-A3B-GGUF',
    content: 'Eleven is prime.',
    stats: {
        model: 'Qwen3.6-35B-A3B-GGUF', retried: true, seconds: 21.4, load_seconds: 15.2,
        input_tokens: 400, output_tokens: 6, tokens_per_second: 44.5, time_to_first_token: 0.3,
    },
};

let resolveRetry: (r: RetryResult) => void = () => {};

beforeEach(() => {
    vi.clearAllMocks();
    // The server holds what the chat shows, so a background refresh never wipes the test's messages.
    mockedApi.getMessages.mockImplementation(async () => {
        const { messages } = useChatStore.getState();
        return { messages, total: messages.length };
    });
    mockedApi.getMessageCount.mockImplementation(async () => useChatStore.getState().messages.length);
    mockedApi.getActiveRuns.mockResolvedValue(NO_ACTIVE_RUNS);
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.listProviders.mockResolvedValue({
        providers: [
            { id: 'lemonade', name: 'This PC', remote: false, privacy_notice: '' },
            { id: 'fireworks', name: 'Cloud', remote: true, privacy_notice: '' },
        ],
    } as never);
    mockedApi.listProviderModels.mockResolvedValue({
        provider: 'lemonade',
        models: [
            { id: 'Gemma-4-E4B-it-GGUF', downloaded: true },
            { id: 'Qwen3.6-35B-A3B-GGUF', downloaded: true },
            { id: 'Not-Downloaded-GGUF', downloaded: false },
        ],
    } as never);
    mockedApi.retryWith.mockImplementation(() => new Promise((res) => { resolveRetry = res; }));
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        sessions: [SESSION],
        currentSessionId: SESSION.id,
        messages: MESSAGES,
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

async function retryWithQwen() {
    render(<ChatView sessionId={SESSION.id} />);
    await act(async () => {});
    await act(async () => {
        fireEvent.click(screen.getByLabelText('Retry with another model'));
    });
    const menu = screen.getByRole('dialog', { name: 'Retry with' });
    expect(within(menu).queryByText('Gemma-4-E4B-it-GGUF')).toBeNull();
    expect(within(menu).queryByText('Not-Downloaded-GGUF')).toBeNull();
    expect(mockedApi.listProviderModels).toHaveBeenCalledTimes(1);
    await act(async () => {
        fireEvent.click(within(menu).getByText('Qwen3.6-35B-A3B-GGUF'));
    });
    expect(mockedApi.retryWith).toHaveBeenCalledWith(SESSION.id, 2, 'Qwen3.6-35B-A3B-GGUF');
}

describe('Retry with…', () => {
    it('shows both answers side by side, labelled by model, with time and speed', async () => {
        await retryWithQwen();
        expect(screen.getByText(/Loading Qwen3.6-35B-A3B-GGUF, then answering/)).toBeTruthy();

        await act(async () => { resolveRetry(RESULT); });
        const original = screen.getByRole('article', { name: 'Answer from Gemma-4-E4B-it-GGUF' });
        const retried = screen.getByRole('article', { name: 'Answer from Qwen3.6-35B-A3B-GGUF' });
        expect(original.textContent).toContain('Seven.');
        expect(original.textContent).toContain('4.0 s');
        expect(original.textContent).toContain('31 tok/s');
        expect(retried.textContent).toContain('Eleven is prime.');
        expect(retried.textContent).toContain('21.4 s');
        expect(retried.textContent).toContain('44.5 tok/s');
    });

    it('continues the chat from the answer the user keeps', async () => {
        mockedApi.chooseRetry.mockResolvedValue({ kept: 'retry', content: RESULT.content, stats: RESULT.stats });
        await retryWithQwen();
        await act(async () => { resolveRetry(RESULT); });

        const retried = screen.getByRole('article', { name: 'Answer from Qwen3.6-35B-A3B-GGUF' });
        await act(async () => {
            fireEvent.click(within(retried).getByText('Keep this answer'));
        });
        expect(mockedApi.chooseRetry).toHaveBeenCalledWith(SESSION.id, 2, 'retry');
        const kept = useChatStore.getState().messages.find((m) => m.id === 2);
        expect(kept?.content).toBe('Eleven is prime.');
        expect(kept?.stats?.model).toBe('Qwen3.6-35B-A3B-GGUF');
        expect(kept?.stats?.retried).toBe(true);
        expect(screen.queryByLabelText('Compare answers')).toBeNull();
    });

    it('queues a message typed while the other model answers', async () => {
        await retryWithQwen();
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'and an even one?' } });
            fireEvent.keyDown(screen.getByLabelText('Message'), { key: 'Enter' });
        });
        expect(mockedApi.sendMessageStream).not.toHaveBeenCalled();
        expect(mockedApi.sendFollowUp).not.toHaveBeenCalled();
        expect(screen.getByText('and an even one?')).toBeTruthy();
    });

    it('says why when the other model could not answer', async () => {
        mockedApi.retryWith.mockRejectedValue(new Error('Qwen3.6-35B-A3B-GGUF did not answer (HTTP 500)'));
        await retryWithQwen();
        await act(async () => {});
        expect(screen.getByRole('alert').textContent).toContain('did not answer');
    });

    it('keeps the original when the user moves on without choosing', async () => {
        mockedApi.chooseRetry.mockResolvedValue({ kept: 'original' });
        mockedApi.sendMessageStream.mockReturnValue(new AbortController());
        await retryWithQwen();
        await act(async () => { resolveRetry(RESULT); });

        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'next question' } });
            fireEvent.keyDown(screen.getByLabelText('Message'), { key: 'Enter' });
        });
        expect(mockedApi.chooseRetry).toHaveBeenCalledWith(SESSION.id, 2, 'original');
        expect(screen.queryByLabelText('Compare answers')).toBeNull();
        expect(mockedApi.sendMessageStream.mock.calls[0][1]).toBe('next question');
    });
});
