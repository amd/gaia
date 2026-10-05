// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Two live-RC bugs in the streaming chat view:
 *  - Esc on the permission prompt must deny the call only, never stop the reply
 *    (pressing it twice used to end the turn as "Cancelled.").
 *  - An answer streamed after a tool call must not split off its first word
 *    into its own paragraph ("The" / blank line / "write was denied ...").
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
import type { Session, StreamEvent } from '../../types';
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

let callbacks: api.StreamCallbacks | null = null;
const respond = vi.fn().mockResolvedValue(undefined);

beforeEach(() => {
    vi.clearAllMocks();
    callbacks = null;
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [], total: 0, total_size_bytes: 0, total_chunks: 0,
    });
    mockedApi.cancelStream.mockResolvedValue(undefined as never);
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        callbacks = cbs as api.StreamCallbacks;
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });

    useNotificationStore.setState({ notifications: [], respondToPermission: respond });
    useChatStore.setState({
        agents: [], sessions: [SESSION], currentSessionId: SESSION.id,
        messages: [], documents: [], isStreaming: false, streamingContent: '',
        agentSteps: [], cards: [], isLoadingMessages: false, pendingPrompt: null,
        systemStatus: null,
    });
});

async function startTurn() {
    render(<ChatView sessionId={SESSION.id} />);
    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'write the file' } });
        fireEvent.click(screen.getByLabelText('Send'));
    });
    expect(callbacks).not.toBeNull();
}

function raisePermission() {
    act(() => {
        useNotificationStore.getState().addNotification({
            id: 'perm-1', type: 'permission_request', agentId: SESSION.id, sessionId: SESSION.id,
            agentName: 'GAIA', title: 'Allow write_file?', message: 'write_file',
            timestamp: 1, read: false, dismissed: false, priority: 'high',
            tool: 'write_file', toolArgs: { file_path: 'C:\\Users\\me\\Documents\\ui_final2.txt' },
        });
    });
}

function finalAnswer(): string {
    const msgs = useChatStore.getState().messages;
    return msgs[msgs.length - 1].content;
}

describe('Esc while a permission prompt is open', () => {
    it('denies the call and leaves the reply running, even when pressed twice', async () => {
        await startTurn();
        raisePermission();

        const prompt = screen.getByRole('alertdialog');
        await act(async () => { fireEvent.keyDown(prompt, { key: 'Escape' }); });
        await act(async () => { fireEvent.keyDown(prompt, { key: 'Escape' }); });

        expect(respond).toHaveBeenCalledTimes(1);
        expect(respond).toHaveBeenCalledWith('perm-1', 'deny');
        expect(mockedApi.cancelStream).not.toHaveBeenCalled();
        expect(useChatStore.getState().isStreaming).toBe(true);
    });

    it('still stops a streaming reply when no prompt is open', async () => {
        await startTurn();

        act(() => { fireEvent.keyDown(window, { key: 'Escape' }); });

        expect(mockedApi.cancelStream).toHaveBeenCalledWith(SESSION.id);
        expect(respond).not.toHaveBeenCalled();
    });
});

describe('answer streamed after a tool call', () => {
    const tool = { type: 'tool_start', tool: 'write_file' } as unknown as StreamEvent;
    const chunk = (content: string) => ({ type: 'chunk', content } as unknown as StreamEvent);
    const thinking = (content: string) => ({ type: 'thinking', content } as unknown as StreamEvent);

    it('keeps the first word in the same paragraph', async () => {
        await startTurn();

        act(() => {
            callbacks!.onAgentEvent(thinking('Let me write the file.'));
            callbacks!.onAgentEvent(tool);
            callbacks!.onAgentEvent(thinking('The write was denied again.'));
            callbacks!.onChunk(chunk('The'));
            callbacks!.onChunk(chunk(' write was denied on your end.'));
            callbacks!.onDone({ type: 'done' } as unknown as StreamEvent);
        });

        expect(finalAnswer()).toBe('The write was denied on your end.');
    });

    it('still starts a new paragraph when text came before the tool', async () => {
        await startTurn();

        act(() => {
            callbacks!.onChunk(chunk('Checking the folder.'));
            callbacks!.onAgentEvent(tool);
            callbacks!.onChunk(chunk('Done'));
            callbacks!.onChunk(chunk(' writing.'));
            callbacks!.onDone({ type: 'done' } as unknown as StreamEvent);
        });

        expect(finalAnswer()).toBe('Checking the folder.\n\nDone writing.');
    });
});
