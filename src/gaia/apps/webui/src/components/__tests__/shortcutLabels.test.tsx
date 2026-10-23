// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Sidebar } from '../Sidebar';
import { GeneralSettings } from '../settings/GeneralSettings';
import { useChatStore } from '../../stores/chatStore';

vi.mock('../../services/api');

function onPlatform(platform: string) {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue(platform);
}

beforeEach(() => {
    useChatStore.setState({
        sessions: [], currentSessionId: null, runningSessionIds: [], pendingDeleteIds: [],
        sidebarOpen: true, sidebarCollapsed: false, settingsSection: null,
        showMemoryDashboard: false, showSchedules: false, messageQueues: {},
    });
});

afterEach(() => vi.restoreAllMocks());

function shortcutList(): string[] {
    return Array.from(document.querySelectorAll('.shortcut-list kbd')).map((k) => k.textContent ?? '');
}

describe('shortcut labels', () => {
    it('show ⌘ on macOS', () => {
        onPlatform('MacIntel');
        render(<GeneralSettings />);
        expect(shortcutList()).toEqual(['Enter', '⇧Enter', 'Esc', '⇧⌘O', '⌘K', '⌘B', '⌘,']);
    });

    it('show Ctrl on Windows and Linux', () => {
        onPlatform('Win32');
        render(<GeneralSettings />);
        expect(shortcutList()).toEqual(['Enter', 'Shift+Enter', 'Esc', 'Ctrl+Shift+O', 'Ctrl+K', 'Ctrl+B', 'Ctrl+,']);
    });

    it('match the platform in sidebar tooltips', () => {
        onPlatform('MacIntel');
        const { unmount } = render(<Sidebar onNewChat={vi.fn()} />);
        expect(screen.getByTitle('New chat (⇧⌘O)')).toBeInTheDocument();
        expect(screen.getByTitle('Settings (⌘,)')).toBeInTheDocument();
        expect(screen.getByTitle('Collapse sidebar (⌘B)')).toBeInTheDocument();
        expect(screen.getByLabelText('Search chats (⌘K)')).toBeInTheDocument();
        unmount();

        onPlatform('Linux x86_64');
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(screen.getByTitle('New chat (Ctrl+Shift+O)')).toBeInTheDocument();
        expect(screen.getByTitle('Settings (Ctrl+,)')).toBeInTheDocument();
    });
});
