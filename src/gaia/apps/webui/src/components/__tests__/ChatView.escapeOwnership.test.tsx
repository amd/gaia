// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Esc pressed for a dialog, menu, popover or inline field must not also stop
 * the running answer or deny a pending tool call. Esc with nothing open still stops.
 */

import { useCallback, useRef, useState } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { Popover } from '../Popover';
import { Sidebar } from '../Sidebar';
import { useEscapeToClose } from '../../hooks/useEscapeToClose';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
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

let callbacks: api.StreamCallbacks | null = null;
const respond = vi.fn().mockResolvedValue(undefined);

beforeEach(() => {
    vi.clearAllMocks();
    callbacks = null;
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [], total: 0, total_size_bytes: 0, total_chunks: 0,
    });
    mockedApi.updateSession.mockResolvedValue(SESSION as never);
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
        systemStatus: null, sidebarOpen: true, sidebarCollapsed: false, settingsSection: null,
        runningSessionIds: [], pendingDeleteIds: [], messageQueues: {},
    });
});

/** A modal built on the shared hook, like Settings or the document library. */
function TestDialog() {
    const [open, setOpen] = useState(true);
    const close = useCallback(() => setOpen(false), []);
    useEscapeToClose(open, close);
    if (!open) return null;
    return <div role="dialog" aria-label="Test dialog"><button type="button">Inside</button></div>;
}

function TestPopover() {
    const anchor = useRef<HTMLButtonElement>(null);
    const [open, setOpen] = useState(true);
    const close = useCallback(() => setOpen(false), []);
    return (
        <>
            <button ref={anchor} type="button">Anchor</button>
            <Popover open={open} onClose={close} anchor={anchor} label="Test menu">
                <button type="button">Item</button>
            </Popover>
        </>
    );
}

async function startTurn(extra?: React.ReactNode) {
    render(<>{extra}<ChatView sessionId={SESSION.id} /></>);
    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'write the file' } });
        fireEvent.click(screen.getByLabelText('Send'));
    });
    expect(callbacks).not.toBeNull();
    expect(useChatStore.getState().isStreaming).toBe(true);
}

function raisePermission() {
    act(() => {
        useNotificationStore.getState().addNotification({
            id: 'perm-1', type: 'permission_request', agentId: SESSION.id, sessionId: SESSION.id,
            agentName: 'GAIA', title: 'Allow write_file?', message: 'write_file',
            timestamp: 1, read: false, dismissed: false, priority: 'high',
            tool: 'write_file', toolArgs: { file_path: 'C:\\Users\\me\\Documents\\notes.txt' },
        });
    });
}

async function esc(target: Element | Window) {
    await act(async () => { fireEvent.keyDown(target, { key: 'Escape' }); });
}

function expectAnswerStillRunning() {
    expect(mockedApi.cancelStream).not.toHaveBeenCalled();
    expect(useChatStore.getState().isStreaming).toBe(true);
}

describe('Esc in the chat rename field', () => {
    it('cancels the rename without stopping the answer', async () => {
        await startTurn();
        fireEvent.click(screen.getByTitle('Rename'));

        await esc(screen.getByLabelText('Chat title'));

        expect(screen.queryByLabelText('Chat title')).not.toBeInTheDocument();
        expectAnswerStillRunning();
    });

    it('does not deny a pending tool call', async () => {
        await startTurn();
        raisePermission();
        fireEvent.click(screen.getByTitle('Rename'));

        await esc(screen.getByLabelText('Chat title'));

        expect(respond).not.toHaveBeenCalled();
        expectAnswerStillRunning();
    });

    it('lets the next Esc stop the answer once the field is gone', async () => {
        await startTurn();
        fireEvent.click(screen.getByTitle('Rename'));

        await esc(screen.getByLabelText('Chat title'));
        await esc(window);

        expect(mockedApi.cancelStream).toHaveBeenCalledTimes(1);
    });
});

describe('Esc with a dialog open', () => {
    it('closes the dialog without stopping the answer, even when the dialog lacks focus', async () => {
        await startTurn(<TestDialog />);

        await esc(document.body);

        expect(screen.queryByRole('dialog', { name: 'Test dialog' })).not.toBeInTheDocument();
        expectAnswerStillRunning();
    });

    it('closes the dialog instead of denying a pending tool call', async () => {
        await startTurn(<TestDialog />);
        raisePermission();

        await esc(screen.getByRole('button', { name: 'Inside' }));

        expect(screen.queryByRole('dialog', { name: 'Test dialog' })).not.toBeInTheDocument();
        expect(respond).not.toHaveBeenCalled();
        expectAnswerStillRunning();
    });

    it('denies on the second Esc and stops on the third, one step at a time', async () => {
        await startTurn(<TestDialog />);
        raisePermission();

        await esc(document.body);
        expect(respond).not.toHaveBeenCalled();

        await esc(screen.getByRole('alertdialog'));
        expect(respond).toHaveBeenCalledTimes(1);
        expect(respond).toHaveBeenCalledWith('perm-1', 'deny');
        expectAnswerStillRunning();

        act(() => { useNotificationStore.setState({ notifications: [] }); });
        await esc(window);
        expect(mockedApi.cancelStream).toHaveBeenCalledTimes(1);
    });
});

describe('Esc with a popover menu open', () => {
    it('closes the popover without stopping the answer or denying', async () => {
        await startTurn(<TestPopover />);
        raisePermission();

        await esc(screen.getByRole('button', { name: 'Item' }));

        expect(screen.queryByRole('button', { name: 'Item' })).not.toBeInTheDocument();
        expect(respond).not.toHaveBeenCalled();
        expectAnswerStillRunning();
    });
});

describe('Esc in the sidebar search', () => {
    it('clears the search without stopping the answer or denying', async () => {
        await startTurn(<Sidebar onNewChat={vi.fn()} />);
        raisePermission();
        const search = screen.getByLabelText('Search chats (Ctrl+K)');
        fireEvent.change(search, { target: { value: 'tax' } });

        await esc(search);

        expect(search).toHaveValue('');
        expect(respond).not.toHaveBeenCalled();
        expectAnswerStillRunning();
    });
});

describe('Esc with nothing else open', () => {
    it('stops the answer from the composer, as designed', async () => {
        await startTurn();

        await esc(screen.getByLabelText('Message'));

        expect(mockedApi.cancelStream).toHaveBeenCalledWith(SESSION.id);
    });

    it('stops the answer only once when pressed repeatedly', async () => {
        await startTurn();

        await esc(window);
        await esc(window);

        expect(mockedApi.cancelStream).toHaveBeenCalledTimes(1);
    });
});
