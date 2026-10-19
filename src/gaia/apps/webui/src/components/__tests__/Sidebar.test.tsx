// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Sidebar } from '../Sidebar';
import { useChatStore } from '../../stores/chatStore';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import type { Session } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

function chat(id: string, title: string, updated: Date): Session {
    return {
        id, title, created_at: updated.toISOString(), updated_at: updated.toISOString(),
        model: '', system_prompt: null, message_count: 2, document_ids: [], inference_remote: false,
    };
}

const SESSIONS = [
    chat('a', 'Fix the build', new Date(2026, 8, 29, 9)),
    chat('b', 'Plan the trip', new Date(2026, 8, 28, 18)),
    chat('c', 'Tax questions', new Date(2026, 7, 1, 12)),
];

beforeEach(() => {
    vi.clearAllMocks();
    // Only Date is faked, so RTL's async helpers keep real timers.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 8, 29, 12));
    mockedApi.deleteSession.mockResolvedValue(undefined);
    useModelStore.setState({
        active: { provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true },
    });
    useChatStore.setState({
        sessions: SESSIONS, currentSessionId: null, runningSessionIds: [], pendingDeleteIds: [],
        sidebarOpen: true, sidebarCollapsed: false, settingsSection: null,
        showMemoryDashboard: false, showSchedules: false,
    });
});

afterEach(() => vi.useRealTimers());

describe('Sidebar', () => {
    it('groups chats by recency', () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        const list = screen.getByRole('navigation', { name: 'Recent chats' });
        const headings = within(list).getAllByRole('heading').map((h) => h.textContent);
        expect(headings).toEqual(['Today', 'Yesterday', 'Older']);
        const today = within(list).getByText('Today').closest('section')!;
        expect(within(today).getByText('Fix the build')).toBeInTheDocument();
        expect(within(today).queryByText('Plan the trip')).not.toBeInTheDocument();
    });

    it('filters chats by search', () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.change(screen.getByRole('searchbox', { name: /Search chats/ }), { target: { value: 'trip' } });
        expect(screen.getByText('Plan the trip')).toBeInTheDocument();
        expect(screen.queryByText('Fix the build')).not.toBeInTheDocument();
    });

    it('New chat calls onNewChat', () => {
        const onNewChat = vi.fn();
        render(<Sidebar onNewChat={onNewChat} />);
        fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
        expect(onNewChat).toHaveBeenCalledOnce();
    });

    it('opens a chat', () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Plan the trip' }));
        expect(useChatStore.getState().currentSessionId).toBe('b');
    });

    it('opening another chat leaves the Memory dashboard', () => {
        useChatStore.setState({ currentSessionId: 'a', showMemoryDashboard: true });
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Plan the trip' }));
        expect(useChatStore.getState().currentSessionId).toBe('b');
        expect(useChatStore.getState().showMemoryDashboard).toBe(false);
    });

    it('clicking the open chat leaves the Memory dashboard too', () => {
        useChatStore.setState({ currentSessionId: 'a', showMemoryDashboard: true, messages: [] });
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Fix the build' }));
        expect(useChatStore.getState().currentSessionId).toBe('a');
        expect(useChatStore.getState().showMemoryDashboard).toBe(false);
    });

    it('deletes only after a confirming click', async () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Delete Tax questions' }));
        expect(mockedApi.deleteSession).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole('button', { name: 'Confirm delete Tax questions' }));
        await waitFor(() => expect(mockedApi.deleteSession).toHaveBeenCalledExactlyOnceWith('c'));
        await waitFor(() => expect(screen.queryByText('Tax questions')).not.toBeInTheDocument());
    });

    it('keeps the chat and says why when delete fails', async () => {
        mockedApi.deleteSession.mockRejectedValue(new Error('Database is locked'));
        vi.spyOn(console, 'error').mockImplementation(() => {});
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Delete Tax questions' }));
        fireEvent.click(screen.getByRole('button', { name: 'Confirm delete Tax questions' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Database is locked');
        expect(screen.getByText('Tax questions')).toBeInTheDocument();
    });

    it('shows where the model runs', () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        const location = screen.getByRole('button', { name: 'Local · Gemma-4-E4B-it-GGUF' });
        fireEvent.click(location);
        expect(useChatStore.getState().settingsSection).toBe('model');
    });

    it('shows a cloud location for a cloud model', () => {
        useModelStore.setState({
            active: { provider: 'fireworks', model: 'fireworks.accounts/fireworks/models/kimi-k2', label: 'Kimi', remote: true, is_default: false },
        });
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(screen.getByRole('button', { name: 'Fireworks AI · kimi-k2' })).toBeInTheDocument();
    });

    it('does not claim Local when the backend cannot tell where chat runs', () => {
        useChatStore.setState({ sessions: SESSIONS.map((s) => ({ ...s, inference_remote: null })) });
        render(<Sidebar onNewChat={vi.fn()} />);
        const location = screen.getByRole('button', { name: 'Gemma-4-E4B-it-GGUF' });
        expect(location.textContent).not.toMatch(/local/i);
    });

    it('shows the cloud provider the backend reports', () => {
        useChatStore.setState({
            sessions: SESSIONS.map((s) => ({ ...s, inference_remote: true, inference_provider_name: 'Fireworks AI' })),
        });
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(screen.getByRole('button', { name: 'Fireworks AI · Gemma-4-E4B-it-GGUF' })).toBeInTheDocument();
    });
});

describe('Sidebar width', () => {
    beforeEach(() => localStorage.removeItem('gaia-chat-sidebar-width'));

    const handle = () => screen.getByRole('separator', { name: 'Resize sidebar' });
    const width = () => screen.getByRole('complementary', { name: 'Chats' }).style.width;

    it('resizes from the keyboard and remembers the width', () => {
        const { unmount } = render(<Sidebar onNewChat={vi.fn()} />);
        expect(handle()).toHaveAttribute('aria-valuenow', '260');
        fireEvent.keyDown(handle(), { key: 'ArrowRight' });
        expect(width()).toBe('276px');
        fireEvent.keyDown(handle(), { key: 'End' });
        expect(handle()).toHaveAttribute('aria-valuenow', '420');
        unmount();
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(width()).toBe('420px');
    });

    it('double-click resets to the default width', () => {
        localStorage.setItem('gaia-chat-sidebar-width', '380');
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(width()).toBe('380px');
        fireEvent.doubleClick(handle());
        expect(width()).toBe('260px');
    });

    it('dragging narrow collapses the sidebar to its rail', () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.pointerDown(handle(), { button: 0, clientX: 260, pointerId: 1 });
        fireEvent.pointerMove(handle(), { clientX: 100, pointerId: 1 });
        fireEvent.pointerUp(handle(), { clientX: 100, pointerId: 1 });
        expect(useChatStore.getState().sidebarCollapsed).toBe(true);
    });

    it('dragging resizes within the limits', () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.pointerDown(handle(), { button: 0, clientX: 260, pointerId: 1 });
        fireEvent.pointerMove(handle(), { clientX: 900, pointerId: 1 });
        fireEvent.pointerUp(handle(), { clientX: 900, pointerId: 1 });
        expect(width()).toBe('420px');
        expect(localStorage.getItem('gaia-chat-sidebar-width')).toBe('420');
    });

    it('keeps the GAIA icon when collapsed, with no handle', () => {
        useChatStore.setState({ sidebarCollapsed: true });
        const { container } = render(<Sidebar onNewChat={vi.fn()} />);
        expect(container.querySelector('.sb-brand img')).not.toBeNull();
        expect(screen.queryByText('GAIA')).not.toBeInTheDocument();
        expect(screen.queryByRole('separator', { name: 'Resize sidebar' })).not.toBeInTheDocument();
    });
});
