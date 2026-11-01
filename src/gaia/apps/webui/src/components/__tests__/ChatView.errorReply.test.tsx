// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A turn that fails before answering (#599): the reply names the cause in
 * plain words, never the raw exception, carries the error banner, and offers
 * "Try again" on the latest failed reply.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Session } from '../../types';
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

let sends: { text: string; cbs: api.StreamCallbacks }[] = [];

beforeEach(() => {
    vi.clearAllMocks();
    sends = [];
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.cancelStream.mockResolvedValue(undefined as never);
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.deleteMessagesFrom.mockResolvedValue(undefined as never);
    mockedApi.sendMessageStream.mockImplementation((_sid, text, cbs) => {
        sends.push({ text: text as string, cbs: cbs as api.StreamCallbacks });
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });

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

async function sendAndFail(error: Error) {
    render(<ChatView sessionId={SESSION.id} />);
    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'summarize the report' } });
        fireEvent.click(screen.getByLabelText('Send'));
    });
    expect(sends).toHaveLength(1);
    await act(async () => {
        sends[0].cbs.onError(error);
    });
    // The finished turn's bubble fades out before the stored reply shows.
    await waitFor(() => expect(document.querySelector('.msg-error')).not.toBeNull());
    const all = useChatStore.getState().messages;
    return all[all.length - 1];
}

/** What the reply says, minus the bug-report link (whose URL pre-fills the raw error for the report). */
const shown = (content: string) => content.split('\n\n---\n')[0];

describe('a failed turn (#599)', () => {
    it('shows the cause and next step, never the raw exception', async () => {
        const reply = await sendAndFail(new Error("KeyError: 'internal_field' in handler.py line 42"));

        expect(reply.role).toBe('assistant');
        expect(reply.failed).toBe(true);
        expect(shown(reply.content)).toContain('Something went wrong while GAIA was answering');
        expect(shown(reply.content)).not.toContain('internal_field');
        expect(reply.content).not.toMatch(/^Error:/);
        // The report link is there for an unknown failure.
        expect(reply.content).toContain('[Report it on GitHub](');
        expect(screen.queryByText(/internal_field/)).toBeNull();
        expect(document.querySelector('.error-banner')).not.toBeNull();
    });

    it('names a context overflow as such', async () => {
        const reply = await sendAndFail(new Error('request (9000 tokens) exceeds the available context size (4096 tokens)'));
        expect(reply.content).toContain('longer than the model can read at once');
        expect(shown(reply.content)).not.toContain('9000 tokens');
    });

    it('drops the raw details from a model-server failure', async () => {
        const reply = await sendAndFail(new Error('Lemonade returned 500: llama-server crashed at 0xdeadbeef'));
        expect(reply.content).toContain('The model server did not answer');
        expect(shown(reply.content)).not.toContain('0xdeadbeef');
        expect(reply.content).not.toContain('Details:');
    });

    it('Try again resends the last question in place', async () => {
        await sendAndFail(new Error('Failed to fetch'));

        const retry = screen.getByRole('button', { name: 'Try again' });
        await act(async () => {
            fireEvent.click(retry);
        });

        await waitFor(() => expect(sends).toHaveLength(2));
        expect(sends[1].text).toBe('summarize the report');
        // The failed reply is gone; the question is asked once more, not twice.
        const messages = useChatStore.getState().messages;
        expect(messages.some((m) => m.failed)).toBe(false);
        expect(messages.filter((m) => m.role === 'user')).toHaveLength(1);
    });

    it('offers Try again only on the latest failed reply, and not while busy', async () => {
        await sendAndFail(new Error('Failed to fetch'));
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        });
        // The resend is streaming: no Try again while busy.
        expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();

        await act(async () => {
            sends[1].cbs.onError(new Error('Failed to fetch'));
        });
        await waitFor(() => expect(screen.getAllByRole('button', { name: 'Try again' })).toHaveLength(1));
    });

    it('a normal answer offers no Try again', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'hi' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        await act(async () => {
            sends[0].cbs.onDone({ type: 'done', content: 'Hello!', message_id: 2 } as never);
        });
        expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
    });
});
