// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The chat list as a queue (#5221). */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Sidebar } from '../Sidebar';
import { useChatStore } from '../../stores/chatStore';
import { useModelStore } from '../../stores/modelStore';
import { useNotificationStore } from '../../stores/notificationStore';
import * as api from '../../services/api';
import type { GaiaNotification } from '../../types/agent';
import type { Session } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

function chat(id: string, title: string, extra: Partial<Session> = {}): Session {
    const at = new Date(2026, 8, 29, 9).toISOString();
    return {
        id, title, created_at: at, updated_at: at, model: '', system_prompt: null,
        message_count: 2, document_ids: [], inference_remote: false, kind: 'chat', ...extra,
    };
}

function prompt(sessionId: string): GaiaNotification {
    return {
        id: `p-${sessionId}`, type: 'permission_request', agentId: 'gaia', agentName: 'GAIA',
        title: 'Run a command?', message: '', timestamp: Date.now(), read: false, dismissed: false,
        priority: 'high', sessionId, tool: 'run_shell_command',
    };
}

const list = () => screen.getByRole('navigation', { name: 'Recent chats' });
const groupOf = (title: string) => within(list()).getByText(title).closest('section')!;
const headings = () => within(list()).getAllByRole('heading').map((h) => h.textContent);

beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 8, 29, 12));
    localStorage.clear();
    mockedApi.markSessionRead.mockImplementation(async (id) => ({ ...chat(id, id), unread: false }));
    mockedApi.pinSession.mockImplementation(async (id, pinned) => ({ ...chat(id, id), pinned }));
    useModelStore.setState({ active: null });
    useNotificationStore.setState({ notifications: [] });
    useChatStore.setState({
        sessions: [chat('a', 'Fix the build'), chat('b', 'Plan the trip')],
        currentSessionId: null, runningSessionIds: [], queuedSessionIds: [], awaitingBySession: {},
        pendingDeleteIds: [], sidebarOpen: true, sidebarCollapsed: false, settingsSection: null,
    });
});

afterEach(() => vi.useRealTimers());

describe('Needs you', () => {
    it('moves a chat paused on a permission prompt to the top as soon as the prompt arrives', () => {
        useChatStore.setState({ runningSessionIds: ['b'] });
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(headings()).toEqual(['Today']);

        act(() => useNotificationStore.getState().addNotification(prompt('b')));

        expect(headings()).toEqual(['Needs you', 'Today']);
        const needsYou = groupOf('Needs you');
        expect(within(needsYou).getByRole('button', { name: 'Plan the trip, Waiting for you' })).toBeInTheDocument();
        expect(within(groupOf('Today')).queryByText('Plan the trip')).not.toBeInTheDocument();
    });

    it('also lists background chats the backend reports as waiting on a question', () => {
        useChatStore.setState({ runningSessionIds: ['a'], awaitingBySession: { a: 'question' } });
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(within(groupOf('Needs you')).getByText('Fix the build')).toBeInTheDocument();
    });

    it('leaves Needs you once the prompt is answered', () => {
        useChatStore.setState({ runningSessionIds: ['b'] });
        useNotificationStore.setState({ notifications: [prompt('b')] });
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(headings()[0]).toBe('Needs you');
        act(() => useNotificationStore.setState({
            notifications: [{ ...prompt('b'), response: 'allow' }],
        }));
        expect(headings()).toEqual(['Today']);
        expect(screen.getByRole('button', { name: 'Plan the trip, Running' })).toBeInTheDocument();
    });
});

describe('row status', () => {
    it('shows running, queued with its place in line, failed, and an unread finished chat', () => {
        useChatStore.setState({
            sessions: [
                chat('a', 'Running one'),
                chat('b', 'Queued one'),
                chat('c', 'Broken one', { last_run_status: 'failed' }),
                chat('d', 'Fresh reply', { last_run_status: 'done', unread: true }),
                chat('e', 'Old reply', { last_run_status: 'done', unread: false }),
            ],
            runningSessionIds: ['a', 'b'], queuedSessionIds: ['b'],
        });
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(screen.getByRole('button', { name: 'Running one, Running' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Queued one, Waiting for the GPU, 1st in line' })).toBeInTheDocument();
        expect(screen.getByText('1st in line')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Broken one, Failed' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Fresh reply, Done, unread' })).toBeInTheDocument();
        // A read reply needs no dot.
        expect(screen.getByRole('button', { name: 'Old reply' })).toBeInTheDocument();
    });

    it('reopening a chat clears its unread state', async () => {
        vi.spyOn(document, 'hasFocus').mockReturnValue(true);
        useChatStore.setState({
            sessions: [chat('a', 'Fresh reply', { last_run_status: 'done', unread: true })],
        });
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Fresh reply, Done, unread' }));

        await waitFor(() => expect(mockedApi.markSessionRead).toHaveBeenCalledWith('a'));
        expect(useChatStore.getState().sessions[0].unread).toBe(false);
        expect(screen.getByRole('button', { name: 'Fresh reply' })).toBeInTheDocument();
    });
});

describe('mark read failure', () => {
    it('reports it once instead of retrying in a loop', async () => {
        vi.spyOn(document, 'hasFocus').mockReturnValue(true);
        mockedApi.markSessionRead.mockRejectedValue(new Error('Backend unreachable'));
        useChatStore.setState({
            sessions: [chat('a', 'Fresh reply', { last_run_status: 'done', unread: true })],
            currentSessionId: 'a',
        });
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(await screen.findByRole('alert')).toHaveTextContent('Backend unreachable');
        await new Promise((r) => setTimeout(r, 50));
        expect(mockedApi.markSessionRead).toHaveBeenCalledTimes(1);
    });
});

describe('eval and scheduled runs', () => {
    beforeEach(() => {
        useChatStore.setState({
            sessions: [
                chat('a', 'My chat'),
                chat('e', 'Eval: rag_quality_01', { kind: 'eval' }),
                chat('s', 'Schedule: morning brief', { kind: 'scheduled' }),
            ],
        });
    });

    it('shows only your own chats by default', () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(screen.getByText('My chat')).toBeInTheDocument();
        expect(screen.queryByText('Eval: rag_quality_01')).not.toBeInTheDocument();
        expect(screen.queryByText('Schedule: morning brief')).not.toBeInTheDocument();
    });

    it('shows them behind the filter, and remembers the choice', () => {
        const { unmount } = render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Show 2 background runs' }));
        expect(screen.getByText('Eval: rag_quality_01')).toBeInTheDocument();
        unmount();
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(screen.getByText('Schedule: morning brief')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Hide background runs' })).toHaveAttribute('aria-pressed', 'true');
    });
});

describe('pinning', () => {
    it('pins a chat into its own group at the top and saves it', async () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Pin Plan the trip' }));
        expect(headings()).toEqual(['Pinned', 'Today']);
        expect(within(groupOf('Pinned')).getByText('Plan the trip')).toBeInTheDocument();
        await waitFor(() => expect(mockedApi.pinSession).toHaveBeenCalledWith('b', true));
    });

    it('rolls back and says so when the pin is not saved', async () => {
        mockedApi.pinSession.mockRejectedValueOnce(new Error('Session not found'));
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Pin Plan the trip' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Session not found');
        expect(headings()).toEqual(['Today']);
    });
});
