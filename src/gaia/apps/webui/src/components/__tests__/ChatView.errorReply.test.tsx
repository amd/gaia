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
import type { Message, Session } from '../../types';
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
// What the backend holds; ids are its own, never the view's temporary ones.
let server: Message[] = [];
let nextId = 100;
// Whether the backend saves the question (and its error reply) when a turn fails.
let persists = true;

const row = (role: 'user' | 'assistant', content: string): Message => ({
    id: ++nextId, session_id: SESSION.id, role, content, created_at: '2026-06-10T00:00:00Z', rag_sources: null,
});

beforeEach(() => {
    vi.clearAllMocks();
    sends = [];
    server = [];
    nextId = 100;
    persists = true;
    mockedApi.getMessages.mockImplementation(async () => ({ messages: [...server], total: server.length }));
    mockedApi.getMessageCount.mockImplementation(async () => server.length);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.cancelStream.mockResolvedValue(undefined as never);
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    // The real endpoint 404s on an id it never saved.
    mockedApi.deleteMessagesFrom.mockImplementation(async (_sid, id) => {
        const at = server.findIndex((m) => m.id === id);
        if (at < 0) throw new Error('Message not found');
        const count = server.length - at;
        server = server.slice(0, at);
        return { deleted: true, count };
    });
    mockedApi.sendMessageStream.mockImplementation((_sid, text, cbs) => {
        sends.push({ text: text as string, cbs: cbs as api.StreamCallbacks });
        if (persists) server.push(row('user', text as string));
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
        messageQueues: {},
        pendingDeleteIds: [],
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
        // The backend saves the same text its error event carries.
        if (persists) server.push(row('assistant', error.message));
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
        // Let the end-of-turn reload land here, not in the next test.
        await waitFor(() => expect(mockedApi.getMessages).toHaveBeenCalledTimes(2));
    });
});

describe('Try again on a failed turn', () => {
    const tryAgain = async () => {
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        });
    };
    const pollServer = async () => {
        await act(async () => {
            document.dispatchEvent(new Event('visibilitychange'));
        });
    };

    it("deletes the server's saved question, not the temporary id, then resends", async () => {
        const reply = await sendAndFail(new Error('Lemonade returned 500'));
        const question = useChatStore.getState().messages.find((m) => m.role === 'user')!;
        expect(question.id).not.toBe(server[0].id);

        await tryAgain();

        await waitFor(() => expect(sends).toHaveLength(2));
        expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledTimes(1);
        expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledWith(SESSION.id, 101);
        expect(sends[1].text).toBe('summarize the report');
        // The new attempt replaced the failed turn on both sides.
        const messages = useChatStore.getState().messages;
        expect(messages.some((m) => m.id === reply.id || m.failed)).toBe(false);
        expect(messages.filter((m) => m.role === 'user')).toHaveLength(1);
        expect(server.map((m) => m.content)).toEqual(['summarize the report']);
    });

    it('resends a question the server never saved without deleting anything', async () => {
        persists = false;
        await sendAndFail(new Error('Failed to fetch'));

        await tryAgain();

        await waitFor(() => expect(sends).toHaveLength(2));
        expect(mockedApi.deleteMessagesFrom).not.toHaveBeenCalled();
        expect(useChatStore.getState().messages.filter((m) => m.role === 'user')).toHaveLength(1);
    });

    it("keeps an earlier identical question that was answered", async () => {
        server = [row('user', 'summarize the report'), row('assistant', 'Here it is.')];
        useChatStore.setState({ messages: [...server] });
        persists = false;
        await sendAndFail(new Error('Failed to fetch'));

        await tryAgain();

        await waitFor(() => expect(sends).toHaveLength(2));
        expect(mockedApi.deleteMessagesFrom).not.toHaveBeenCalled();
        expect(server.map((m) => m.content)).toEqual(['summarize the report', 'Here it is.']);
    });

    it('still works after the list reloads from the server', async () => {
        await sendAndFail(new Error('Lemonade returned 500'));
        await pollServer();
        // The reload swapped in the server's rows, ids included.
        await waitFor(() => expect(useChatStore.getState().messages.map((m) => m.id)).toEqual([101, 102]));
        expect(useChatStore.getState().messages[1].failed).toBe(true);

        await tryAgain();

        await waitFor(() => expect(sends).toHaveLength(2));
        expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledWith(SESSION.id, 101);
        expect(useChatStore.getState().messages.some((m) => m.failed)).toBe(false);
    });

    it('offers no Try again when the answer may still finish', async () => {
        // The send got no answer in time; the run may still be going on the server.
        const timedOut = Object.assign(new api.StreamTimeoutError('send', 30_000), {
            endpoint: 'send', timeoutMs: 30_000, message: 'GAIA did not start answering within 30 s.',
        });
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'summarize the report' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        await act(async () => {
            sends[0].cbs.onError(timedOut);
        });
        const last = () => useChatStore.getState().messages[useChatStore.getState().messages.length - 1];
        await waitFor(() => expect(last()?.content).toContain('did not start this answer'));
        expect(last()?.failed).toBe(false);
        expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
    });

    it('sends once when Try again is clicked twice', async () => {
        await sendAndFail(new Error('Lemonade returned 500'));
        const button = screen.getByRole('button', { name: 'Try again' });

        await act(async () => {
            fireEvent.click(button);
            fireEvent.click(button);
        });

        await waitFor(() => expect(sends).toHaveLength(2));
        expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledTimes(1);
        expect(useChatStore.getState().messages.filter((m) => m.role === 'user')).toHaveLength(1);
    });

    it('keeps the failed turn and says so when the resend cannot reach GAIA', async () => {
        const reply = await sendAndFail(new Error('Lemonade returned 500'));
        mockedApi.getMessages.mockRejectedValueOnce(new TypeError('Failed to fetch'));
        const { useNotificationStore } = await import('../../stores/notificationStore');

        await tryAgain();

        await waitFor(() => expect(
            useNotificationStore.getState().notifications.some((n) => n.title === 'Your message was not sent again' && n.message.startsWith('Lost the connection to GAIA')),
        ).toBe(true));
        expect(sends).toHaveLength(1);
        expect(useChatStore.getState().messages.some((m) => m.id === reply.id)).toBe(true);
        expect(screen.getByRole('button', { name: 'Try again' })).not.toBeNull();
    });
});

describe('Try again never deletes an answer written since the failure', () => {
    const pollServer = async () => {
        await act(async () => {
            document.dispatchEvent(new Event('visibilitychange'));
        });
    };
    const answeredElsewhere = () => {
        server = [row('user', 'summarize the report'), row('assistant', 'REAL ANSWER')];
    };

    it('shows an answer from another window as a normal answer after the poll', async () => {
        persists = false;
        await sendAndFail(new TypeError('Failed to fetch'));
        answeredElsewhere();

        await pollServer();

        await waitFor(() => expect(useChatStore.getState().messages.map((m) => m.content)).toEqual(['summarize the report', 'REAL ANSWER']));
        expect(useChatStore.getState().messages.some((m) => m.failed)).toBe(false);
        expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
        expect(document.querySelector('.msg-error')).toBeNull();
        expect(server.map((m) => m.content)).toEqual(['summarize the report', 'REAL ANSWER']);
    });

    it('does not delete that answer when Try again is pressed before the poll', async () => {
        persists = false;
        await sendAndFail(new TypeError('Failed to fetch'));
        answeredElsewhere();

        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        });

        await waitFor(() => expect(useChatStore.getState().messages.map((m) => m.content)).toEqual(['summarize the report', 'REAL ANSWER']));
        expect(mockedApi.deleteMessagesFrom).not.toHaveBeenCalled();
        expect(sends).toHaveLength(1);
        expect(server.map((m) => m.content)).toEqual(['summarize the report', 'REAL ANSWER']);
    });

    it('keeps a run that finished after this view lost its response', async () => {
        // The request reached the server and its run finished; only the response was lost.
        persists = false;
        await sendAndFail(new TypeError('Failed to fetch'));
        server = [row('user', 'summarize the report'), row('assistant', 'REAL ANSWER')];

        await pollServer();
        await waitFor(() => expect(useChatStore.getState().messages[1]?.content).toBe('REAL ANSWER'));
        expect(useChatStore.getState().messages[1].failed).toBeFalsy();
        expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
        expect(server.map((m) => m.content)).toEqual(['summarize the report', 'REAL ANSWER']);
    });

    it('asks again through the background queue when the chat is left mid-delete', async () => {
        await sendAndFail(new Error('Lemonade returned 500'));
        let release!: () => void;
        const gate = new Promise<void>((r) => { release = r; });
        const realDelete = mockedApi.deleteMessagesFrom.getMockImplementation()!;
        mockedApi.deleteMessagesFrom.mockImplementationOnce(async (sid, id) => {
            await gate;
            return realDelete(sid, id);
        });

        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        });
        await waitFor(() => expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledTimes(1));
        await act(async () => {
            useChatStore.setState({ currentSessionId: 'other-chat' });
            release();
        });

        await waitFor(() => expect(useChatStore.getState().messageQueues[SESSION.id]?.map((m) => m.text)).toEqual(['summarize the report']));
        expect(server).toEqual([]);
    });

    it('does not queue the question again when the chat is deleted mid-delete', async () => {
        await sendAndFail(new Error('Lemonade returned 500'));
        let release!: () => void;
        const gate = new Promise<void>((r) => { release = r; });
        const realDelete = mockedApi.deleteMessagesFrom.getMockImplementation()!;
        mockedApi.deleteMessagesFrom.mockImplementationOnce(async (sid, id) => {
            await gate;
            return realDelete(sid, id);
        });

        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        });
        await waitFor(() => expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledTimes(1));
        await act(async () => {
            useChatStore.getState().removeSession(SESSION.id);
            release();
        });

        await waitFor(() => expect(server).toEqual([]));
        await act(async () => { await Promise.resolve(); });
        expect(useChatStore.getState().messageQueues[SESSION.id]).toBeUndefined();
        expect(sends).toHaveLength(1);
    });
});
