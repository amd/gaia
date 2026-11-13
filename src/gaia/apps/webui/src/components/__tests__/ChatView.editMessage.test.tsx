// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A sent message can be edited in place and sent again, Up-arrow in an empty
 * message box opens the last one, and an answer can be asked for again with
 * the same model (#7356). All three go through Resend's path: the old turn is
 * deleted on the server, then the text is sent.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Message, Session, SystemStatus } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1', title: 'Canals', created_at: '2026-06-10T00:00:00Z', updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen', system_prompt: null, message_count: 4, document_ids: [], agent_type: 'gaia',
};
const READY = { lemonade_running: true, initialized: true, init_state: 'ready', init_tasks: [] } as unknown as SystemStatus;

const row = (id: number, role: 'user' | 'assistant', content: string): Message => ({
    id, session_id: SESSION.id, role, content, created_at: '2026-06-10T00:00:00Z', rag_sources: null,
});

let server: Message[] = [];
let sends: string[] = [];

beforeEach(() => {
    vi.clearAllMocks();
    server = [
        row(1, 'user', 'Name a canal.'), row(2, 'assistant', 'The Suez Canal.'),
        row(3, 'user', 'How long is the Panama canl?'), row(4, 'assistant', 'About 82 km.'),
    ];
    sends = [];
    mockedApi.getMessages.mockImplementation(async () => ({ messages: [...server], total: server.length }));
    mockedApi.getMessageCount.mockImplementation(async () => server.length);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.getSystemStatus.mockResolvedValue(READY);
    mockedApi.deleteMessagesFrom.mockImplementation(async (_sid, id) => {
        const at = server.findIndex((m) => m.id === id);
        const count = server.length - at;
        server = server.slice(0, at);
        return { deleted: true, count };
    });
    mockedApi.sendMessageStream.mockImplementation((_sid, text) => {
        sends.push(text as string);
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        sessions: [SESSION], currentSessionId: SESSION.id, messages: [...server], documents: [],
        isStreaming: false, streamingContent: '', agentSteps: [], cards: [], isLoadingMessages: false,
        pendingPrompt: null, systemStatus: READY, messageQueues: {}, pendingDeleteIds: [],
    });
});

async function show() {
    render(<ChatView sessionId={SESSION.id} />);
    await waitFor(() => expect(screen.getByText('About 82 km.')).toBeTruthy());
}

const editor = () => screen.getByRole('textbox', { name: 'Edit your message' }) as HTMLTextAreaElement;

describe('editing a sent message', () => {
    it('opens the message in place and sends the edited text from that point', async () => {
        await show();
        const edits = screen.getAllByRole('button', { name: 'Edit message' });
        expect(edits).toHaveLength(2);
        fireEvent.click(edits[1]);

        expect(editor().value).toBe('How long is the Panama canl?');
        expect(document.activeElement).toBe(editor());
        expect((within(editor().parentElement!).getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(true);

        fireEvent.change(editor(), { target: { value: 'How long is the Panama canal?' } });
        await act(async () => {
            fireEvent.keyDown(editor(), { key: 'Enter' });
        });

        await waitFor(() => expect(sends).toEqual(['How long is the Panama canal?']));
        expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledWith(SESSION.id, 3);
        expect(server.map((m) => m.id)).toEqual([1, 2]);
    });

    it('puts the message back on Escape, sending nothing', async () => {
        await show();
        fireEvent.click(screen.getAllByRole('button', { name: 'Edit message' })[1]);
        fireEvent.change(editor(), { target: { value: 'something else' } });
        fireEvent.keyDown(editor(), { key: 'Escape' });

        expect(screen.queryByRole('textbox', { name: 'Edit your message' })).toBeNull();
        expect(screen.getByText('How long is the Panama canl?')).toBeTruthy();
        expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Message' }));
        expect(sends).toEqual([]);
    });

    it('opens the last sent message on Up-arrow in an empty message box', async () => {
        await show();
        const box = screen.getByRole('textbox', { name: 'Message' });
        fireEvent.keyDown(box, { key: 'ArrowUp' });
        expect(editor().value).toBe('How long is the Panama canl?');
    });

    it('leaves Up-arrow alone when the box has text', async () => {
        await show();
        const box = screen.getByRole('textbox', { name: 'Message' });
        fireEvent.change(box, { target: { value: 'draft' } });
        fireEvent.keyDown(box, { key: 'ArrowUp' });
        expect(screen.queryByRole('textbox', { name: 'Edit your message' })).toBeNull();
    });
});

describe('editing a message with an attachment', () => {
    const LINK = '[standup.wav](/api/files/uploads/standup-25a14da6.wav)';

    beforeEach(() => {
        server[2] = row(3, 'user', `Transcribe this recording\n\n${LINK}`);
        useChatStore.setState({ messages: [...server] });
    });

    it('shows only the typed text, with the file as a chip', async () => {
        await show();
        fireEvent.keyDown(screen.getByRole('textbox', { name: 'Message' }), { key: 'ArrowUp' });
        expect(editor().value).toBe('Transcribe this recording');
        const chips = screen.getByRole('list', { name: 'Attached files' });
        expect(chips.textContent).toBe('standup.wav');
    });

    it('sends the file again with the edited text', async () => {
        await show();
        fireEvent.keyDown(screen.getByRole('textbox', { name: 'Message' }), { key: 'ArrowUp' });
        fireEvent.change(editor(), { target: { value: 'Summarize this recording' } });
        await act(async () => {
            fireEvent.keyDown(editor(), { key: 'Enter' });
        });
        await waitFor(() => expect(sends).toEqual([`Summarize this recording\n\n${LINK}`]));
    });
});

describe('resending an earlier message', () => {
    it('says how many later messages go away, and deletes nothing until confirmed', async () => {
        await show();
        fireEvent.click(screen.getAllByRole('button', { name: 'Resend message' })[0]);

        const ask = screen.getByRole('alertdialog');
        expect(ask.textContent).toContain('This also deletes the 2 later messages in this chat.');
        expect(mockedApi.deleteMessagesFrom).not.toHaveBeenCalled();
        fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' }));
        expect(screen.queryByRole('alertdialog')).toBeNull();
        expect(screen.getByText('About 82 km.')).toBeTruthy();
        expect(sends).toEqual([]);

        fireEvent.click(screen.getAllByRole('button', { name: 'Resend message' })[0]);
        await act(async () => {
            fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete and send' }));
        });
        await waitFor(() => expect(sends).toEqual(['Name a canal.']));
        expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledWith(SESSION.id, 1);
    });

    it('asks the same before sending an edit of an earlier message', async () => {
        await show();
        fireEvent.click(screen.getAllByRole('button', { name: 'Edit message' })[0]);
        fireEvent.change(editor(), { target: { value: 'Name a long canal.' } });
        await act(async () => {
            fireEvent.keyDown(editor(), { key: 'Enter' });
        });

        expect(screen.getByRole('alertdialog').textContent).toContain('2 later messages');
        expect(screen.getAllByRole('button', { name: 'Cancel' })).toHaveLength(1);
        expect(document.querySelectorAll('.msg-edit .msg-edit-actions')).toHaveLength(0);
        expect(editor().value).toBe('Name a long canal.');
        expect(mockedApi.deleteMessagesFrom).not.toHaveBeenCalled();
        expect(sends).toEqual([]);

        await act(async () => {
            fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete and send' }));
        });
        await waitFor(() => expect(sends).toEqual(['Name a long canal.']));
    });

    it('resends the last question without asking', async () => {
        await show();
        await act(async () => {
            fireEvent.click(screen.getAllByRole('button', { name: 'Resend message' })[1]);
        });
        expect(screen.queryByRole('alertdialog')).toBeNull();
        await waitFor(() => expect(sends).toEqual(['How long is the Panama canl?']));
    });
});

describe('an edit is never lost', () => {
    async function editAndSend(text: string) {
        fireEvent.click(screen.getAllByRole('button', { name: 'Edit message' })[1]);
        fireEvent.change(editor(), { target: { value: text } });
        await act(async () => {
            fireEvent.keyDown(editor(), { key: 'Enter' });
        });
    }

    it('keeps the editor and its text when the backend is still starting', async () => {
        await show();
        const starting = { ...READY, init_state: 'initializing', init_tasks: [{ name: 'model', status: 'running' }] } as unknown as SystemStatus;
        mockedApi.getSystemStatus.mockResolvedValue(starting);
        fireEvent.click(screen.getAllByRole('button', { name: 'Edit message' })[1]);
        fireEvent.change(editor(), { target: { value: 'How long is the Panama canal?' } });
        act(() => useChatStore.setState({ systemStatus: starting }));
        await act(async () => {
            fireEvent.keyDown(editor(), { key: 'Enter' });
        });

        await waitFor(() => expect(mockedApi.getSystemStatus).toHaveBeenCalled());
        expect(sends).toEqual([]);
        expect(mockedApi.deleteMessagesFrom).not.toHaveBeenCalled();
        expect(editor().value).toBe('How long is the Panama canal?');
    });

    it('keeps the editor and its text when the chat could not be read', async () => {
        await show();
        mockedApi.getMessages.mockRejectedValueOnce(new Error('connection reset'));
        await editAndSend('How long is the Panama canal?');

        await waitFor(() => expect(mockedApi.getMessages).toHaveBeenCalledTimes(2));
        expect(sends).toEqual([]);
        expect(editor().value).toBe('How long is the Panama canal?');
        fireEvent.keyDown(editor(), { key: 'Enter' });
        await waitFor(() => expect(sends).toEqual(['How long is the Panama canal?']));
    });

    it('brings the typed text back when a running turn closed the editor', async () => {
        await show();
        fireEvent.click(screen.getAllByRole('button', { name: 'Edit message' })[1]);
        fireEvent.change(editor(), { target: { value: 'half typed' } });
        act(() => useChatStore.setState({ isStreaming: true }));
        expect(screen.queryByRole('textbox', { name: 'Edit your message' })).toBeNull();
        act(() => useChatStore.setState({ isStreaming: false }));
        expect(editor().value).toBe('half typed');
    });
});

describe('Try again on an answer', () => {
    it('asks the last question again', async () => {
        await show();
        const buttons = screen.getAllByRole('button', { name: 'Try again' });
        expect(buttons).toHaveLength(1);
        await act(async () => {
            fireEvent.click(buttons[0]);
        });

        await waitFor(() => expect(sends).toEqual(['How long is the Panama canl?']));
        expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledWith(SESSION.id, 3);
    });

    it('asks again even while the answer still has its temporary id', async () => {
        await show();
        // As a turn leaves it when its done event carried no message id, before the refetch.
        act(() => useChatStore.setState({ messages: [...server.slice(0, 3), row(Date.now() + 1, 'assistant', 'About 82 km.')] }));
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        });

        await waitFor(() => expect(sends).toEqual(['How long is the Panama canl?']));
        expect(mockedApi.deleteMessagesFrom).toHaveBeenCalledWith(SESSION.id, 3);
    });
});
