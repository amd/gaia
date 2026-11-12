// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
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

function chat(id: string, title: string, pinned = false): Session {
    const now = new Date().toISOString();
    return {
        id, title, pinned, created_at: now, updated_at: now,
        model: '', system_prompt: null, message_count: 2, document_ids: [], inference_remote: false,
    };
}

function setPhone(phone: boolean) {
    Object.defineProperty(window, 'matchMedia', {
        configurable: true,
        value: (query: string) => ({
            matches: phone && query === '(max-width: 768px)',
            media: query,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
        }),
    });
}

const more = (title: string) => screen.getByRole('button', { name: `More actions for ${title}` });
const menu = () => screen.getByRole('dialog', { name: 'Chat actions' });

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.updateSession.mockResolvedValue(chat('a', 'x'));
    mockedApi.pinSession.mockResolvedValue(undefined as never);
    mockedApi.deleteSession.mockResolvedValue(undefined);
    useModelStore.setState({
        active: { provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true },
    });
    useChatStore.setState({
        sessions: [chat('a', 'Trip plan'), chat('b', 'Tax questions')],
        currentSessionId: null, runningSessionIds: [], pendingDeleteIds: [],
        sidebarOpen: true, sidebarCollapsed: false, messageQueues: {},
    });
});

afterEach(() => {
    delete (window as { matchMedia?: unknown }).matchMedia;
});

describe('Sidebar row menu', () => {
    it('shows one actions button per row on a phone, and the three icons on desktop', () => {
        setPhone(true);
        const { unmount } = render(<Sidebar onNewChat={vi.fn()} />);
        expect(more('Trip plan')).toHaveAttribute('aria-haspopup', 'dialog');
        expect(more('Trip plan')).toHaveAttribute('aria-expanded', 'false');
        expect(screen.queryByRole('button', { name: 'Rename Trip plan' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Pin Trip plan' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Delete Trip plan' })).not.toBeInTheDocument();
        unmount();

        setPhone(false);
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(screen.queryByRole('button', { name: 'More actions for Trip plan' })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Rename Trip plan' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Pin Trip plan' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Delete Trip plan' })).toBeInTheDocument();
    });

    it('opens the menu without opening the chat, and Escape closes it back onto the button', () => {
        setPhone(true);
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(more('Trip plan'));
        expect(useChatStore.getState().currentSessionId).toBeNull();
        expect(useChatStore.getState().sidebarOpen).toBe(true);
        expect(more('Trip plan')).toHaveAttribute('aria-expanded', 'true');
        expect(within(menu()).getByRole('button', { name: 'Rename' })).toHaveFocus();

        fireEvent.keyDown(document, { key: 'Escape' });
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        expect(more('Trip plan')).toHaveFocus();
    });

    it('starts the inline rename from the menu', async () => {
        setPhone(true);
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(more('Tax questions'));
        fireEvent.click(within(menu()).getByRole('button', { name: 'Rename' }));
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        const input = screen.getByRole('textbox', { name: 'Rename Tax questions' });
        fireEvent.change(input, { target: { value: 'Taxes 2026' } });
        fireEvent.keyDown(input, { key: 'Enter' });
        await waitFor(() => expect(mockedApi.updateSession).toHaveBeenCalledExactlyOnceWith(
            'b', { title: 'Taxes 2026', title_is_custom: true },
        ));
    });

    it('pins and unpins from the menu', async () => {
        setPhone(true);
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(more('Trip plan'));
        fireEvent.click(within(menu()).getByRole('button', { name: 'Pin' }));
        await waitFor(() => expect(mockedApi.pinSession).toHaveBeenCalledExactlyOnceWith('a', true));
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

        fireEvent.click(more('Trip plan'));
        fireEvent.click(within(menu()).getByRole('button', { name: 'Unpin' }));
        await waitFor(() => expect(mockedApi.pinSession).toHaveBeenLastCalledWith('a', false));
    });

    it('deletes only after a second tap inside the menu', async () => {
        setPhone(true);
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(more('Trip plan'));
        fireEvent.click(within(menu()).getByRole('button', { name: 'Delete' }));
        expect(mockedApi.deleteSession).not.toHaveBeenCalled();
        const confirm = within(menu()).getByRole('button', { name: 'Delete? Tap again' });
        fireEvent.click(confirm);
        await waitFor(() => expect(mockedApi.deleteSession).toHaveBeenCalledExactlyOnceWith('a'));
        expect(useChatStore.getState().currentSessionId).toBeNull();
    });

    it('keeps the unsent-messages wording on the confirm step', () => {
        setPhone(true);
        useChatStore.setState({
            messageQueues: { a: [{ id: 'q1', text: 'hi', state: 'queued' }, { id: 'q2', text: 'yo', state: 'queued' }] } as never,
        });
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(more('Trip plan'));
        fireEvent.click(within(menu()).getByRole('button', { name: 'Delete' }));
        expect(within(menu()).getByRole('button', { name: 'Delete 2 unsent? Tap again' })).toBeInTheDocument();
        expect(mockedApi.deleteSession).not.toHaveBeenCalled();
    });

    it('forgets a half-confirmed delete when the menu closes', () => {
        setPhone(true);
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(more('Trip plan'));
        fireEvent.click(within(menu()).getByRole('button', { name: 'Delete' }));
        fireEvent.keyDown(document, { key: 'Escape' });
        fireEvent.click(more('Trip plan'));
        expect(within(menu()).getByRole('button', { name: 'Delete' })).toBeInTheDocument();
    });
});

describe('Phone rename and pin feedback', () => {
    const renameFromMenu = (title: string) => {
        fireEvent.click(more(title));
        fireEvent.click(within(menu()).getByRole('button', { name: 'Rename' }));
        return screen.getByRole('textbox', { name: `Rename ${title}` });
    };

    it('saves a phone rename with the Save button', async () => {
        setPhone(true);
        render(<Sidebar onNewChat={vi.fn()} />);
        const field = renameFromMenu('Trip plan');
        fireEvent.change(field, { target: { value: 'Lisbon trip' } });
        const save = screen.getByRole('button', { name: 'Save name' });
        expect(fireEvent.pointerDown(save)).toBe(false);
        fireEvent.click(save);
        await waitFor(() => expect(mockedApi.updateSession).toHaveBeenCalledWith('a', { title: 'Lisbon trip', title_is_custom: true }));
        expect(screen.queryByRole('textbox', { name: /Rename/ })).not.toBeInTheDocument();
    });

    it('cancels a phone rename without saving, even though tapping Cancel would blur the field', () => {
        setPhone(true);
        render(<Sidebar onNewChat={vi.fn()} />);
        const field = renameFromMenu('Trip plan');
        fireEvent.change(field, { target: { value: 'Something else' } });
        const cancel = screen.getByRole('button', { name: 'Cancel rename' });
        expect(fireEvent.pointerDown(cancel)).toBe(false);
        expect(fireEvent.mouseDown(cancel)).toBe(false);
        fireEvent.click(cancel);
        expect(mockedApi.updateSession).not.toHaveBeenCalled();
        expect(screen.getByRole('button', { name: 'More actions for Trip plan' })).toBeInTheDocument();
    });

    it('shows no Save or Cancel buttons on desktop', () => {
        setPhone(false);
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Rename Trip plan' }));
        expect(screen.getByRole('textbox', { name: 'Rename Trip plan' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Save name' })).not.toBeInTheDocument();
    });

    it('follows a chat pinned from the menu and marks it', async () => {
        setPhone(true);
        const scrolled = vi.spyOn(HTMLElement.prototype, 'scrollIntoView');
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(more('Tax questions'));
        fireEvent.click(within(menu()).getByRole('button', { name: 'Pin' }));
        await waitFor(() => expect(scrolled).toHaveBeenCalled());
        const row = screen.getByRole('button', { name: 'More actions for Tax questions' }).closest('li');
        expect(row).toHaveClass('is-moved');
    });
});
