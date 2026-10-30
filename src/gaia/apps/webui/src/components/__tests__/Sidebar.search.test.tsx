// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Sidebar } from '../Sidebar';
import { useChatStore } from '../../stores/chatStore';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import type { Session } from '../../types';
import { splitMatch } from '../../utils/chatSearch';

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

const TRIP_IN_TAX = {
    session_id: 'c', title: 'Tax questions', field: 'message' as const,
    snippet: { before: 'can I deduct the ', match: 'trip', after: ' to the conference?' },
};

function searchbox() {
    return screen.getByRole('searchbox', { name: /Search chats/ });
}

function list() {
    return screen.getByRole('navigation', { name: 'Recent chats' });
}

async function type(value: string) {
    fireEvent.change(searchbox(), { target: { value } });
}

async function settle() {
    await act(async () => {
        vi.advanceTimersByTime(200);
    });
}

beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
    vi.setSystemTime(new Date(2026, 8, 29, 12));
    mockedApi.searchSessions.mockResolvedValue({ query: '', results: [] });
    useModelStore.setState({
        active: { provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true },
    });
    useChatStore.setState({
        sessions: SESSIONS, currentSessionId: null, runningSessionIds: [], pendingDeleteIds: [],
        sidebarOpen: true, sidebarCollapsed: false, settingsSection: null,
        showMemoryDashboard: false, showSchedules: false, messageQueues: {},
    });
});

afterEach(() => vi.useRealTimers());

describe('Sidebar chat search', () => {
    it('asks the backend once, 200 ms after the last keystroke', async () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        await type('t');
        await type('tr');
        await type('tri');
        act(() => { vi.advanceTimersByTime(199); });
        expect(mockedApi.searchSessions).not.toHaveBeenCalled();
        await settle();
        expect(mockedApi.searchSessions).toHaveBeenCalledOnce();
        expect(mockedApi.searchSessions.mock.calls[0][0]).toBe('tri');
        // Background runs stay out of the count, as they stay out of the list.
        expect(mockedApi.searchSessions.mock.calls[0][1]).toBe(false);
    });

    it('filters titles before the backend answers', async () => {
        mockedApi.searchSessions.mockReturnValue(new Promise(() => {}));
        render(<Sidebar onNewChat={vi.fn()} />);
        await type('TRIP');
        expect(within(list()).getByRole('button', { name: 'Plan the trip' })).toBeInTheDocument();
        expect(within(list()).queryByRole('button', { name: /Tax questions/ })).not.toBeInTheDocument();
    });

    it('merges title matches with chats that matched inside a message', async () => {
        mockedApi.searchSessions.mockResolvedValue({ query: 'trip', results: [TRIP_IN_TAX] });
        render(<Sidebar onNewChat={vi.fn()} />);
        await type('trip');
        await settle();
        const rows = within(list()).getAllByRole('button', { name: /^(Plan the trip|Tax questions)/ });
        expect(rows.map((r) => r.getAttribute('title'))).toEqual(['Plan the trip', 'Tax questions']);
        expect(within(list()).queryByText('Fix the build')).not.toBeInTheDocument();

        const tax = rows[1];
        expect(tax).toHaveAccessibleName('Tax questions, can I deduct the trip to the conference?');
        const marks = tax.querySelectorAll('mark');
        expect([...marks].map((m) => m.textContent)).toEqual(['trip']);
        // The title match is highlighted too.
        expect(rows[0].querySelector('mark')?.textContent).toBe('trip');
        expect(screen.getByRole('status')).toHaveTextContent('2 chats match');
    });

    it('drops results that belong to an older query', async () => {
        mockedApi.searchSessions.mockResolvedValue({ query: 'trip', results: [TRIP_IN_TAX] });
        render(<Sidebar onNewChat={vi.fn()} />);
        await type('trip');
        await settle();
        await type('tripod');
        expect(within(list()).queryByRole('button', { name: /Tax questions/ })).not.toBeInTheDocument();
    });

    it('moves through results with the arrow keys and opens one with Enter', async () => {
        mockedApi.searchSessions.mockResolvedValue({ query: 'trip', results: [TRIP_IN_TAX] });
        render(<Sidebar onNewChat={vi.fn()} />);
        await type('trip');
        await settle();
        const box = searchbox();
        expect(box).not.toHaveAttribute('aria-activedescendant');

        fireEvent.keyDown(box, { key: 'ArrowDown' });
        expect(box).toHaveAttribute('aria-activedescendant', 'sb-result-b');
        fireEvent.keyDown(box, { key: 'ArrowDown' });
        expect(box).toHaveAttribute('aria-activedescendant', 'sb-result-c');
        fireEvent.keyDown(box, { key: 'ArrowDown' });
        expect(box).toHaveAttribute('aria-activedescendant', 'sb-result-c');
        expect(document.getElementById('sb-result-c')!.closest('li')).toHaveClass('is-kbd-active');
        fireEvent.keyDown(box, { key: 'ArrowUp' });
        expect(box).toHaveAttribute('aria-activedescendant', 'sb-result-b');

        fireEvent.keyDown(box, { key: 'ArrowDown' });
        fireEvent.keyDown(box, { key: 'Enter' });
        expect(useChatStore.getState().currentSessionId).toBe('c');
    });

    it('Enter with nothing picked opens the first result', async () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        await type('trip');
        fireEvent.keyDown(searchbox(), { key: 'Enter' });
        expect(useChatStore.getState().currentSessionId).toBe('b');
    });

    it('Escape clears the search', async () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        await type('trip');
        fireEvent.keyDown(searchbox(), { key: 'Escape' });
        expect(searchbox()).toHaveValue('');
        expect(within(list()).getByRole('button', { name: 'Fix the build' })).toBeInTheDocument();
    });

    it('says when nothing matches, only once the backend has answered', async () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        await type('zebra');
        expect(within(list()).getByText('Searching inside chats…')).toBeInTheDocument();
        expect(screen.getByRole('status')).toHaveTextContent('');
        await settle();
        expect(within(list()).getByText('No chats match.')).toBeInTheDocument();
        expect(screen.getByRole('status')).toHaveTextContent('No chats match.');
    });

    it('says when only the newest matching chats are listed', async () => {
        mockedApi.searchSessions.mockResolvedValue({ query: 'trip', results: [TRIP_IN_TAX], truncated: true });
        render(<Sidebar onNewChat={vi.fn()} />);
        await type('trip');
        expect(within(list()).queryByText(/newest chats that mention this/)).not.toBeInTheDocument();
        await settle();
        expect(within(list()).getByText(/Showing the 1 newest chats that mention this/)).toBeInTheDocument();
    });

    it('keeps title matches and reports the error when the backend search fails', async () => {
        mockedApi.searchSessions.mockRejectedValue(new Error('server down'));
        render(<Sidebar onNewChat={vi.fn()} />);
        await type('trip');
        await settle();
        expect(within(list()).getByRole('button', { name: 'Plan the trip' })).toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent('Could not search inside chats: server down');
    });
});

describe('splitMatch', () => {
    it('folds case and accents both ways and keeps the original spelling', () => {
        expect(splitMatch('Send the Résumé', 'resume')).toEqual({ before: 'Send the ', match: 'Résumé', after: '' });
        expect(splitMatch('the resume file', 'RÉSUMÉ')).toEqual({ before: 'the ', match: 'resume', after: ' file' });
        expect(splitMatch('😀 emoji then word', 'word')).toEqual({ before: '😀 emoji then ', match: 'word', after: '' });
        expect(splitMatch('nothing', 'zzz')).toBeNull();
        expect(splitMatch('Die Straße hier', 'strasse')).toEqual({ before: 'Die ', match: 'Straße', after: ' hier' });
    });
});
