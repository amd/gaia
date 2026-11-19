// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Sidebar } from '../Sidebar';
import { PrivacySettings } from '../settings/PrivacySettings';
import { useChatStore } from '../../stores/chatStore';
import { useModelStore } from '../../stores/modelStore';
import { stripIsolates } from '../../i18n/registry';
import * as api from '../../services/api';
import type { Session } from '../../types';

vi.mock('../../services/api');
vi.mock('../settings/UndoHistorySettings', () => ({ UndoHistorySettings: () => null }));

const EN_NOTE = 'This also removes it from Memory. What GAIA learned about you stays in About you.';
const AR_NOTE = 'يزيلها هذا من الذاكرة أيضًا. يبقى ما تعلّمه GAIA عنك في «About you».';
const EN_ALL = 'Deletes every chat and its messages, and removes them from Memory. Documents are kept, and what GAIA learned about you stays in About you.';
const AR_ALL = 'يحذف كل محادثة ورسائلها ويزيلها من الذاكرة. تبقى المستندات، ويبقى ما تعلّمه GAIA عنك في «About you».';

function chat(id: string, title: string): Session {
    const now = new Date().toISOString();
    return {
        id, title, created_at: now, updated_at: now,
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

const plain = (text: string | null | undefined) => stripIsolates(text ?? '');

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.deleteSession).mockResolvedValue(undefined);
    vi.mocked(api.getSettings).mockResolvedValue({ network_policy: 'cloud_allowed' } as never);
    useModelStore.setState({
        active: { provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true },
    });
    useChatStore.setState({
        sessions: [chat('a', 'Trip plan')], currentSessionId: null, runningSessionIds: [], pendingDeleteIds: [],
        sidebarOpen: true, sidebarCollapsed: false, messageQueues: {}, systemStatus: null,
    });
});

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
    delete (window as { matchMedia?: unknown }).matchMedia;
});

describe('deleting a chat says what happens to Memory', () => {
    it('adds the Memory line to the desktop confirm step, and not before it', () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        const del = screen.getByRole('button', { name: 'Delete Trip plan' });
        expect(plain(del.getAttribute('title'))).not.toContain(EN_NOTE);
        fireEvent.click(del);
        const confirm = screen.getByRole('button', { name: 'Confirm delete Trip plan' });
        expect(plain(confirm.getAttribute('title'))).toBe(`Click again to delete\n${EN_NOTE}`);
        expect(api.deleteSession).not.toHaveBeenCalled();
    });

    it('keeps the Memory line when the confirm also warns about unsent messages', () => {
        useChatStore.setState({ messageQueues: { a: [{ id: 'q1', text: 'hi', state: 'queued' }] } as never });
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Delete Trip plan' }));
        const title = plain(screen.getByRole('button', { name: /Confirm delete Trip plan/ }).getAttribute('title'));
        expect(title).toBe(`Click again to delete this chat and its 1 unsent message\n${EN_NOTE}`);
    });

    it('shows the Memory line in the phone menu only on the confirm step', () => {
        setPhone(true);
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'More actions for Trip plan' }));
        const menu = screen.getByRole('dialog', { name: 'Chat actions' });
        expect(within(menu).queryByText(EN_NOTE)).toBeNull();
        fireEvent.click(within(menu).getByRole('button', { name: 'Delete' }));
        expect(within(menu).getByText(EN_NOTE)).toBeInTheDocument();
    });

    it('says it in Arabic, with the English after it', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        render(<Sidebar onNewChat={vi.fn()} />);
        const del = document.querySelector('.sb-row-delete')!;
        fireEvent.click(del);
        const title = plain(document.querySelector('.sb-row-delete')!.getAttribute('title'));
        expect(title).toContain(AR_NOTE);
        expect(title).toContain(`(${EN_NOTE})`);
    });

    it('says it in the Settings > Privacy delete-all warning, in both languages', () => {
        const { unmount } = render(<PrivacySettings />);
        expect(plain(document.querySelector('.danger-warning')?.textContent)).toBe(EN_ALL);
        unmount();

        act(() => useChatStore.getState().setLocale('ar'));
        render(<PrivacySettings />);
        const warning = plain(document.querySelector('.danger-warning')?.textContent);
        expect(warning).toContain(AR_ALL);
        expect(warning).toContain(`(${EN_ALL})`);
    });
});
