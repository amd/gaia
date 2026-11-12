// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A card is approved as it reads, so it must read as what will run: characters
// that don't show are shown as codes, every line of a command is there, and a
// command whose text hides what it runs is shown decoded beside the original.

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionPrompt } from '../PermissionPrompt';
import { useNotificationStore, PATH_ACCESS_TOOL } from '../../stores/notificationStore';
import type { GaiaNotification } from '../../types/agent';

const originalRespond = useNotificationStore.getState().respondToPermission;

function request(over: Partial<GaiaNotification> = {}): GaiaNotification {
    return {
        id: 'perm-1', type: 'permission_request', agentId: 'chat-1', sessionId: 'chat-1',
        agentName: 'GAIA', title: 'Allow run_shell_command?', message: '', timestamp: 1,
        read: false, dismissed: false, priority: 'high', tool: 'run_shell_command',
        toolArgs: { command: 'git status' }, ...over,
    };
}

function show(over: Partial<GaiaNotification>) {
    useNotificationStore.getState().addNotification(request(over));
    render(<PermissionPrompt sessionId="chat-1" />);
    return screen.getByRole('alertdialog');
}

/** UTF-16LE base64, as PowerShell's -EncodedCommand takes it. */
function encoded(script: string): string {
    const bytes = new Uint8Array(script.length * 2);
    for (let i = 0; i < script.length; i++) {
        bytes[i * 2] = script.charCodeAt(i) & 0xff;
        bytes[i * 2 + 1] = script.charCodeAt(i) >> 8;
    }
    return btoa(String.fromCharCode(...bytes));
}

beforeEach(() => {
    useNotificationStore.setState({ notifications: [], respondToPermission: vi.fn() });
});
afterEach(() => {
    useNotificationStore.setState({ notifications: [], respondToPermission: originalRespond });
});

describe('a permission card shows what will run', () => {
    it('shows a zero-width character in a file name as a code, with a note', () => {
        const card = show({ tool: 'write_file', toolArgs: { file_path: 'C:\\w\\inv\u200Boice.txt', content: 'x' } });
        expect(card).toHaveTextContent('inv[U+200B]oice.txt');
        expect(card.textContent).not.toContain('\u200B');
        expect(screen.getByTestId('perm-hidden-note')).toBeInTheDocument();
    });

    it('shows a right-to-left override in a path prompt as a code', () => {
        const card = show({ tool: PATH_ACCESS_TOOL, toolArgs: { path: 'C:\\w\\report\u202Etxt.exe', kind: 'file' } });
        expect(card).toHaveTextContent('report[U+202E]txt.exe');
        expect(card.textContent).not.toContain('\u202E');
    });

    it('shows every line of a multi-line command', () => {
        const card = show({ toolArgs: { command: 'echo ok > n1.txt\r\ndel plain.txt' } });
        const shown = card.querySelector('.perm-card-exact, .perm-card-detail code')?.textContent ?? '';
        expect(shown).toContain('echo ok > n1.txt');
        expect(shown).toContain('del plain.txt');
    });

    it('decodes -EncodedCommand beside the original', () => {
        const payload = encoded('Remove-Item C:\\w\\plain.txt');
        show({ toolArgs: { command: `powershell -NoProfile -EncodedCommand ${payload}` } });
        expect(screen.getByTestId('perm-decoded')).toHaveTextContent('Remove-Item C:\\w\\plain.txt');
        expect(screen.getByRole('alertdialog')).toHaveTextContent(payload);
    });

    it.each(['-e', '-ec', '-enc', '-EncodedCommand'])('decodes the %s spelling', (flag) => {
        show({ toolArgs: { command: `pwsh ${flag} ${encoded('Get-Process')}` } });
        expect(screen.getByTestId('perm-decoded')).toHaveTextContent('Get-Process');
    });

    it("shows cmd's reading of a caret-escaped command", () => {
        show({ toolArgs: { command: 'de^l plain.txt' } });
        expect(screen.getByTestId('perm-cmd-reading')).toHaveTextContent('del plain.txt');
    });

    it('adds nothing to an ordinary command', () => {
        show({ toolArgs: { command: 'git status' } });
        expect(screen.queryByTestId('perm-hidden-note')).not.toBeInTheDocument();
        expect(screen.queryByTestId('perm-decoded')).not.toBeInTheDocument();
        expect(screen.queryByTestId('perm-cmd-reading')).not.toBeInTheDocument();
    });

    it('shows a hidden character in code as a code', () => {
        show({ tool: 'run_python', toolArgs: { code: 'print("a\u2066b")' } });
        expect(screen.getByTestId('perm-code').textContent).toBe('print("a[U+2066]b")');
        expect(screen.getByTestId('perm-hidden-note')).toBeInTheDocument();
    });

    it.each([
        ['its label', { alwaysScope: 'write_file on invoice\u202Etxt.exe', alwaysLabel: 'writing invoice\u202Etxt.exe' }],
        ['its scope alone', { alwaysScope: 'write_file on invoice\u202Etxt.exe' }],
    ])('marks a hidden character on the always-allow button, named by %s', (_name, grant) => {
        const card = show({
            tool: 'write_file', toolArgs: { file_path: 'C:\\w\\invoice\u202Etxt.exe', content: 'x' }, ...grant,
        });
        const always = screen.getByRole('button', { name: /Always allow/ });
        expect(always).toHaveTextContent('invoice[U+202E]txt.exe');
        expect(card.textContent).not.toContain('\u202E');
    });

    it('marks hidden characters in the details and the folder a command runs in', () => {
        const card = show({
            toolArgs: { command: 'dir', working_directory: 'C:\\w\\a\u200Bb', _risk_note: 'Not checked.' },
        });
        fireEvent.click(screen.getByRole('button', { name: 'Details' }));
        expect(card).toHaveTextContent('C:\\w\\a[U+200B]b');
        expect(card.textContent).not.toContain('\u200B');
    });
});
