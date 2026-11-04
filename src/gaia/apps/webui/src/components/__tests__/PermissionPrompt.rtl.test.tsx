// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// In Arabic the code a prompt asks about still reads left to right, exactly as it runs.

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { PermissionPrompt } from '../PermissionPrompt';
import { useNotificationStore, PATH_ACCESS_TOOL } from '../../stores/notificationStore';
import { useChatStore } from '../../stores/chatStore';
import type { GaiaNotification } from '../../types/agent';

const CODE = 'p = r"C:\\Users\\you\\notes.txt"\nt = open(p, encoding="utf-8").read()';

function prompt(over: Partial<GaiaNotification>): void {
    useNotificationStore.getState().addNotification({
        id: 'p1', type: 'permission_request', agentId: 's', sessionId: 's', agentName: 'GAIA',
        title: '', message: '', timestamp: 1, read: false, dismissed: false, priority: 'high',
        ...over,
    } as GaiaNotification);
    render(<PermissionPrompt sessionId="s" />);
}

beforeEach(() => {
    useChatStore.setState({ locale: 'ar' });
    document.documentElement.dir = 'rtl';
    useNotificationStore.setState({ notifications: [] });
});
afterEach(() => {
    useChatStore.setState({ locale: 'en' });
    document.documentElement.dir = 'ltr';
    useNotificationStore.setState({ notifications: [] });
});

describe('permission prompt in Arabic', () => {
    it('keeps the code to run left to right, and the details too', () => {
        prompt({ tool: 'run_python', toolLabel: 'running python', toolArgs: { code: CODE } });
        expect(screen.getByTestId('perm-code')).toHaveAttribute('dir', 'ltr');
        fireEvent.click(screen.getByRole('button', { name: 'Details' }));
        for (const pre of document.querySelectorAll('pre')) expect(pre, pre.outerHTML).toHaveAttribute('dir', 'ltr');
    });

    it('isolates the English title so its question mark stays at its end', () => {
        prompt({ tool: 'run_python', toolLabel: 'running python', toolArgs: { code: CODE } });
        const title = screen.getByRole('heading');
        expect(title.querySelector('bdi')).toHaveTextContent(/^Allow running python\?$/);
    });

    it('keeps a shell command, its folder and its always-allow scope left to right', () => {
        prompt({
            tool: 'run_shell_command', toolLabel: 'running a command', alwaysScope: 'git status',
            toolArgs: { command: 'git status --short', working_directory: 'C:\\repo', _risk_note: 'Runs outside the sandbox.' },
        });
        const card = screen.getByRole('alertdialog');
        expect(card.querySelector('.perm-card-exact')).toHaveAttribute('dir', 'ltr');
        const codes = [...card.querySelectorAll('code')];
        expect(codes.map((c) => c.textContent)).toEqual(expect.arrayContaining(['C:\\repo', 'git status']));
        for (const c of codes) expect(c, c.outerHTML).toHaveAttribute('dir', 'ltr');
    });

    it('keeps a relayed command left to right', () => {
        prompt({ tool: 'run_shell_command', summary: 'dir /b "C:\\data"\nin C:\\data\nLists files.' });
        expect(screen.getByRole('alertdialog').querySelector('.perm-card-exact')).toHaveAttribute('dir', 'ltr');
    });

    it('keeps a path left to right and the English question whole', () => {
        prompt({
            tool: PATH_ACCESS_TOOL, title: 'Let GAIA use this folder?',
            message: 'GAIA wants to use the folder C:\\data. Allow it for this chat?',
            toolArgs: { path: 'C:\\data\\reports' },
        });
        const card = screen.getByRole('alertdialog');
        expect(card.querySelector('.perm-card-detail code')).toHaveAttribute('dir', 'ltr');
        expect(screen.getByRole('heading').querySelector('bdi')).toHaveTextContent(/^Let GAIA use this folder\?$/);
    });
});
