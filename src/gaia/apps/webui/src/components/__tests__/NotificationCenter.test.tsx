// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Regression tests for the notification center's store subscription.
 *
 * Opening the panel blanked the whole Agent UI: the visible-notifications
 * selector filters into a fresh array, and zustand v5 hands that straight to
 * useSyncExternalStore, which re-renders forever on an uncached snapshot
 * ("Maximum update depth exceeded", React #185). These tests mount the panel
 * for real so a selector that stops being cached crashes the suite, not a user.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NotificationCenter } from '../NotificationCenter';
import { useNotificationStore, PATH_ACCESS_TOOL } from '../../stores/notificationStore';
import type { GaiaNotification } from '../../types/agent';
import { stripIsolates } from '../../i18n/registry';
import { ARABIC_MONTH, ENGLISH_MONTH, resetUiLocale, setUiLocale } from '../../test/dates';
import notificationCss from '../NotificationCenter.css?raw';

const notif = (id: string, message: string): GaiaNotification => ({
    id,
    type: 'info',
    agentId: 'gaia',
    agentName: 'GAIA',
    title: 'Heads up',
    message,
    timestamp: Date.now(),
    read: false,
    dismissed: false,
    priority: 'medium',
});

beforeEach(() => {
    useNotificationStore.setState({ notifications: [], typeFilter: null });
});

describe('NotificationCenter', () => {
    it('renders an empty panel without looping', () => {
        expect(() => render(<NotificationCenter onClose={vi.fn()} />)).not.toThrow();
        expect(screen.getByText('No notifications')).toBeTruthy();
    });

    it('renders notifications without looping', () => {
        useNotificationStore.setState({
            notifications: [notif('n-1', 'Model finished loading'), notif('n-2', 'Document indexed')],
        });
        expect(() => render(<NotificationCenter onClose={vi.fn()} />)).not.toThrow();
        expect(screen.getByText('Model finished loading')).toBeTruthy();
        expect(screen.getByText('Document indexed')).toBeTruthy();
    });

    it('hides dismissed notifications', () => {
        useNotificationStore.setState({
            notifications: [
                { ...notif('n-1', 'Still here'), dismissed: false },
                { ...notif('n-2', 'Gone'), dismissed: true },
            ],
        });
        render(<NotificationCenter onClose={vi.fn()} />);
        expect(screen.getByText('Still here')).toBeTruthy();
        expect(screen.queryByText('Gone')).toBeNull();
    });
});

describe('NotificationCenter dates follow the interface language', () => {
    afterEach(resetUiLocale);

    it('shows a week-old notification date in Arabic in an Arabic UI', () => {
        setUiLocale('ar');
        useNotificationStore.setState({
            notifications: [{ ...notif('n-1', 'Old news'), timestamp: Date.now() - 30 * 86_400_000 }],
        });
        const { container } = render(<NotificationCenter onClose={vi.fn()} />);
        const time = stripIsolates(container.querySelector('.notification-time')!.textContent ?? '');
        expect(time).toMatch(ARABIC_MONTH);
        expect(time).not.toMatch(ENGLISH_MONTH);
    });

    it('says how long ago in Arabic for a recent one', () => {
        setUiLocale('ar');
        useNotificationStore.setState({
            notifications: [{ ...notif('n-1', 'Fresh'), timestamp: Date.now() - 5 * 60_000 }],
        });
        const { container } = render(<NotificationCenter onClose={vi.fn()} />);
        expect(stripIsolates(container.querySelector('.notification-time')!.textContent ?? '')).toContain('قبل 5 د');
    });
});

describe('NotificationCenter permission prompts follow the interface language', () => {
    afterEach(resetUiLocale);

    const prompt = (extra: Partial<GaiaNotification>): GaiaNotification => ({
        ...notif('perm-1', 'GAIA needs your OK before running python.'),
        type: 'permission_request',
        title: 'Allow running python?',
        priority: 'high',
        ...extra,
    });

    it('words a tool prompt in Arabic, the tool kept left to right', () => {
        setUiLocale('ar');
        useNotificationStore.setState({
            notifications: [prompt({ tool: 'some_new_tool', toolLabel: 'editing file', toolArgs: { file_path: 'a.txt' } })],
        });
        const { container } = render(<NotificationCenter onClose={vi.fn()} />);
        const title = container.querySelector('.notification-title')!;
        expect(title.textContent).toContain('هل تسمح بـ');
        expect(title.querySelector('bdi[dir="ltr"]')).toHaveTextContent(/^editing file$/);
        expect(container.querySelector('.notification-message')).toHaveTextContent('يحتاج GAIA إلى موافقتك قبل');
    });

    it('words a path prompt in Arabic, the path kept left to right', () => {
        setUiLocale('ar');
        useNotificationStore.setState({
            notifications: [prompt({
                tool: PATH_ACCESS_TOOL, title: 'Let GAIA use this folder?',
                message: 'GAIA wants to use the folder C:\\data. Allow it for this chat?',
                toolArgs: { path: 'C:\\data', kind: 'folder' },
            })],
        });
        const { container } = render(<NotificationCenter onClose={vi.fn()} />);
        expect(container.querySelector('.notification-title')).toHaveTextContent('هل تسمح لـ GAIA باستخدام هذا المجلد؟');
        const message = container.querySelector('.notification-message')!;
        expect(message).toHaveTextContent('يريد GAIA استخدام المجلد');
        expect(message.querySelector('bdi[dir="ltr"]')).toHaveTextContent(/^C:\\data$/);
    });

    it('reads the same as before in English', () => {
        useNotificationStore.setState({
            notifications: [prompt({ tool: 'some_new_tool', toolLabel: 'editing file', toolArgs: { file_path: 'a.txt' } })],
        });
        const { container } = render(<NotificationCenter onClose={vi.fn()} />);
        expect(stripIsolates(container.querySelector('.notification-title')!.textContent!)).toBe('Allow editing file?');
        expect(stripIsolates(container.querySelector('.notification-message')!.textContent!)).toBe('GAIA needs your OK before editing file.');
    });
});

describe('a permission entry', () => {
    it('says what it was in words, with no raw tool id', () => {
        useNotificationStore.setState({
            notifications: [{ ...notif('perm-9', 'GAIA needs your OK before running Python.'), type: 'permission_request', tool: 'run_python', toolLabel: 'running Python', toolArgs: { code: 'print(1)' } }],
        });
        const { container } = render(<NotificationCenter onClose={vi.fn()} />);
        expect(container.querySelector('.notification-item')).not.toHaveTextContent('run_python');
    });

    it('wraps its filter tabs instead of running them off the edge', () => {
        expect(notificationCss).toMatch(/\.notification-filter-bar \{[^}]*flex-wrap: wrap/);
    });
});

describe('clicking a notification that names a chat', () => {
    it('opens that chat and marks the notification read', () => {
        const onOpenChat = vi.fn();
        useNotificationStore.setState({
            notifications: [{ ...notif('done-1', 'Three headlines today.'), title: 'Scheduled task "news" finished', sessionId: 'chat-1' }],
        });
        render(<NotificationCenter onClose={vi.fn()} onOpenChat={onOpenChat} />);
        fireEvent.click(screen.getByText('Scheduled task "news" finished'));
        expect(onOpenChat).toHaveBeenCalledExactlyOnceWith('chat-1');
        expect(useNotificationStore.getState().notifications[0].read).toBe(true);
    });

    it('only marks it read when there is no chat to open', () => {
        const onOpenChat = vi.fn();
        useNotificationStore.setState({ notifications: [notif('plain-1', 'Document indexed')] });
        render(<NotificationCenter onClose={vi.fn()} onOpenChat={onOpenChat} />);
        fireEvent.click(screen.getByText('Document indexed'));
        expect(onOpenChat).not.toHaveBeenCalled();
        expect(useNotificationStore.getState().notifications[0].read).toBe(true);
    });
});
