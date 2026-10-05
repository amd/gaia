// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionPrompt, formatCountdown } from '../PermissionPrompt';
import { useNotificationStore } from '../../stores/notificationStore';

const originalRespond = useNotificationStore.getState().respondToPermission;
const respond = vi.fn().mockResolvedValue(undefined);

function raise(timeoutSeconds?: number) {
    useNotificationStore.getState().addNotification({
        id: 'perm-1', type: 'permission_request', agentId: 's', sessionId: 's', agentName: 'GAIA',
        title: 'Allow write_file?', message: 'The agent wants to execute: write_file',
        timestamp: 1, read: false, dismissed: false, priority: 'high',
        tool: 'write_file', toolArgs: { file_path: 'C:\\Users\\me\\notes.txt' },
        timeoutSeconds,
    });
}

function focusedComposer(): HTMLTextAreaElement {
    const composer = document.createElement('textarea');
    document.body.appendChild(composer);
    composer.focus();
    return composer;
}

beforeEach(() => {
    vi.clearAllMocks();
    useNotificationStore.setState({ notifications: [], respondToPermission: respond });
});
afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = '';
    useNotificationStore.setState({ notifications: [], respondToPermission: originalRespond });
});

describe('PermissionPrompt keyboard', () => {
    it('does not approve on Enter typed into the composer', () => {
        const composer = focusedComposer();
        raise();
        render(<PermissionPrompt sessionId="s" />);

        fireEvent.keyDown(composer, { key: 'Enter' });
        fireEvent.keyDown(window, { key: 'Enter' });
        fireEvent.keyDown(document.activeElement ?? window, { key: 'Enter' });

        expect(respond).not.toHaveBeenCalled();
    });

    it('takes focus from the composer, and not onto a button', () => {
        const composer = focusedComposer();
        raise();
        render(<PermissionPrompt sessionId="s" />);

        expect(document.activeElement).not.toBe(composer);
        expect(document.activeElement).toBe(screen.getByRole('alertdialog'));
    });

    it('denies on Escape', () => {
        raise();
        render(<PermissionPrompt sessionId="s" />);

        fireEvent.keyDown(window, { key: 'Escape' });

        expect(respond).toHaveBeenCalledExactlyOnceWith('perm-1', 'deny');
    });

    it('approves only through a button', () => {
        raise();
        render(<PermissionPrompt sessionId="s" />);

        fireEvent.click(screen.getByRole('button', { name: 'Allow once' }));

        expect(respond).toHaveBeenCalledExactlyOnceWith('perm-1', 'allow');
    });
});

describe('PermissionPrompt countdown', () => {
    it('shows the backend wait in minutes and reports the backend denial when it runs out', () => {
        vi.useFakeTimers();
        raise(600);
        render(<PermissionPrompt sessionId="s" />);
        expect(screen.getByText('10:00')).toBeInTheDocument();

        act(() => { vi.advanceTimersByTime(599_000); });
        expect(screen.getByText('1s')).toBeInTheDocument();

        act(() => { vi.advanceTimersByTime(1_000); });
        expect(screen.getByText('No answer in time, so GAIA was told no.')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Allow once' })).not.toBeInTheDocument();
        expect(respond).not.toHaveBeenCalled();
    });

    it('follows the deadline when a background tab throttles its ticks', () => {
        vi.useFakeTimers();
        raise(600);
        render(<PermissionPrompt sessionId="s" />);

        act(() => {
            vi.setSystemTime(Date.now() + 300_000);
            vi.advanceTimersByTime(1_000);
        });

        expect(screen.getByText('4:59')).toBeInTheDocument();
    });

    it('shows no countdown when the backend sent no timeout', () => {
        raise(undefined);
        render(<PermissionPrompt sessionId="s" />);
        expect(screen.queryByTitle('Denied automatically when the time runs out')).not.toBeInTheDocument();
    });

    it('formats seconds and minutes', () => {
        expect(formatCountdown(45)).toBe('45s');
        expect(formatCountdown(60)).toBe('1:00');
        expect(formatCountdown(599)).toBe('9:59');
    });
});
