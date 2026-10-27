// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionPrompt } from '../PermissionPrompt';
import { useNotificationStore } from '../../stores/notificationStore';
import type { GaiaNotification } from '../../types/agent';

const originalRespond = useNotificationStore.getState().respondToPermission;
const respond = vi.fn();

function request(over: Partial<GaiaNotification> = {}): GaiaNotification {
    return {
        id: 'perm-1', type: 'permission_request', agentId: 'chat-1', sessionId: 'chat-1',
        agentName: 'GAIA', title: 'Allow run_shell_command?', message: '', timestamp: 1,
        read: false, dismissed: false, priority: 'high', tool: 'run_shell_command',
        toolArgs: { command: 'git status' }, ...over,
    };
}

beforeEach(() => {
    respond.mockReset().mockResolvedValue(undefined);
    useNotificationStore.setState({ notifications: [], respondToPermission: respond });
});
afterEach(() => {
    useNotificationStore.setState({ notifications: [], respondToPermission: originalRespond });
});

describe('PermissionPrompt', () => {
    it('names the site a browser pane read would show the model', () => {
        useNotificationStore.getState().addNotification(request({
            tool: 'browser_snapshot', toolLabel: 'running browser snapshot',
            toolArgs: { _site: 'https://example.org' },
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.getByRole('alertdialog')).toHaveTextContent('https://example.org');
    });

    it('shows short code whole, with no expand control', () => {
        useNotificationStore.getState().addNotification(request({
            tool: 'run_python', toolArgs: { code: 'print(1)\nprint(2)' },
            summary: 'print(1)\nprint(2)',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.getByTestId('perm-code').textContent).toBe('print(1)\nprint(2)');
        expect(screen.queryByRole('button', { name: /Show all/ })).not.toBeInTheDocument();
    });

    it('collapses long code and shows every line on "Show all"', () => {
        const code = Array.from({ length: 30 }, (_, i) => `line_${i + 1} = ${i + 1}`).join('\n');
        useNotificationStore.getState().addNotification(request({
            tool: 'run_python', toolArgs: { code },
            // What a relayed agent's summary looks like: the middle elided.
            summary: 'line_1 = 1\n…[18 more lines]…\nline_30 = 30',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        const block = screen.getByTestId('perm-code');
        expect(block.textContent).toContain('line_12 = 12');
        expect(block.textContent).not.toContain('line_13 = 13');
        // The code block replaces the shortened summary rather than repeating it.
        expect(screen.getByRole('alertdialog')).not.toHaveTextContent('more lines');
        fireEvent.click(screen.getByRole('button', { name: 'Show all 30 lines' }));
        expect(screen.getByTestId('perm-code').textContent).toBe(code);
        expect(screen.getByRole('button', { name: 'Show less' })).toHaveAttribute('aria-expanded', 'true');
    });

    it('splits a relayed command summary with no args into the command and its risk note', () => {
        useNotificationStore.getState().addNotification(request({
            toolArgs: undefined,
            summary: 'powershell -Command "Get-ChildItem"\nCan run any code, with your permissions.',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(document.querySelector('.perm-card-exact')?.textContent).toBe('powershell -Command "Get-ChildItem"');
        expect(screen.getByRole('note')).toHaveTextContent('Can run any code, with your permissions.');
    });

    it("keeps a relayed command's folder, extras and note apart, as the server sends them", () => {
        useNotificationStore.getState().addNotification(request({
            toolArgs: undefined,
            summary: 'Get-ChildItem\nin C:\\Users\\me\n(timeout=30)\nCan run any code, with your permissions.',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(document.querySelector('.perm-card-exact')?.textContent).toBe('Get-ChildItem');
        expect(screen.getByRole('note').textContent).toBe('Can run any code, with your permissions.');
        expect(screen.getByText('C:\\Users\\me').tagName).toBe('CODE');
        expect(screen.getByText('(timeout=30)')).toBeInTheDocument();
    });

    it('keeps a multi-line relayed command whole in the code block', () => {
        useNotificationStore.getState().addNotification(request({
            toolArgs: undefined,
            summary: "cat <<'EOF' > notes.txt\nin the morning\nEOF\nin /home/me\nCan run any code, with your permissions.",
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(document.querySelector('.perm-card-exact')?.textContent).toBe("cat <<'EOF' > notes.txt\nin the morning\nEOF");
        expect(screen.getByText('/home/me').tagName).toBe('CODE');
    });

    it('reads a relayed command with no risk note: the folder is last', () => {
        useNotificationStore.getState().addNotification(request({
            toolArgs: undefined,
            summary: 'git status\nin /repo',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(document.querySelector('.perm-card-exact')?.textContent).toBe('git status');
        expect(screen.getByText('/repo').tagName).toBe('CODE');
        expect(screen.queryByRole('note')).not.toBeInTheDocument();
    });

    it('keeps a no-args summary for other tools as plain text', () => {
        useNotificationStore.getState().addNotification(request({
            tool: 'send_email', toolArgs: undefined, summary: 'Send to a@b.c\nSubject: hi',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(document.querySelector('.perm-card-exact')).toBeNull();
        expect(document.querySelector('.perm-card-detail')?.textContent).toBe('Send to a@b.c\nSubject: hi');
    });

    it('renders nothing when this chat has no pending request', () => {
        useNotificationStore.getState().addNotification(request({ sessionId: 'other-chat' }));
        const { container } = render(<PermissionPrompt sessionId="chat-1" />);
        expect(container).toBeEmptyDOMElement();
    });

    it('offers allow once, always (with its scope) and deny', () => {
        useNotificationStore.getState().addNotification(request({ alwaysScope: 'git status' }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.getByRole('alertdialog')).toHaveTextContent('Allow GAIA to use run_shell_command?');
        expect(screen.getByRole('button', { name: 'Allow once' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Always allow git status in this chat' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Deny' })).toBeInTheDocument();
    });

    it('words the question and the always option without tool names', () => {
        useNotificationStore.getState().addNotification(request({
            tool: 'write_file',
            toolLabel: 'writing file',
            toolArgs: { file_path: '/home/u/prompt-test.txt', content: 'hi' },
            alwaysScope: 'write_file on prompt-test.txt',
            alwaysLabel: 'writing prompt-test.txt',
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        const dialog = screen.getByRole('alertdialog');
        expect(screen.getByRole('heading')).toHaveTextContent('Allow writing file?');
        expect(screen.getByRole('button', { name: 'Always allow writing prompt-test.txt in this chat' })).toBeInTheDocument();
        expect(dialog).not.toHaveTextContent('write_file on');
    });

    it("shows a relayed agent's summary when the call came without args", () => {
        const summary = `Run 'write_file' with file_path="notes.md"?`;
        useNotificationStore.getState().addNotification(request({ tool: 'write_file', toolArgs: undefined, summary }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.getByRole('alertdialog')).toHaveTextContent(summary);
    });

    it('prefers the args over the summary when both are present', () => {
        useNotificationStore.getState().addNotification(request({ summary: 'should not show' }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.getByRole('alertdialog')).toHaveTextContent('git status');
        expect(screen.getByRole('alertdialog')).not.toHaveTextContent('should not show');
    });

    it('shows a one-time call verbatim with its folder and risk note, and no always', () => {
        const command = 'git push   origin main';
        useNotificationStore.getState().addNotification(request({
            toolArgs: {
                command,
                working_directory: '/home/u/proj',
                _risk_note: 'Sends your commits to the remote repository.',
            },
        }));
        render(<PermissionPrompt sessionId="chat-1" />);
        const dialog = screen.getByRole('alertdialog');
        // Unwrapped and uncollapsed: the exact text the shell receives.
        expect(dialog.querySelector('pre.perm-card-exact')?.textContent).toBe(command);
        expect(dialog).toHaveTextContent('in /home/u/proj');
        expect(screen.getByRole('note')).toHaveTextContent('Sends your commits to the remote repository.');
        expect(screen.queryByRole('button', { name: /Always allow/ })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Allow once' })).toBeInTheDocument();
    });

    it('offers no always option without a scope', () => {
        useNotificationStore.getState().addNotification(request());
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.queryByRole('button', { name: /Always allow/ })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Allow once' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Deny' })).toBeInTheDocument();
    });

    it.each([
        ['Allow once', 'allow'],
        [/Always allow/, 'always'],
        ['Deny', 'deny'],
    ] as const)('clicking %s answers %s', async (name, decision) => {
        useNotificationStore.getState().addNotification(request({ alwaysScope: 'git status' }));
        render(<PermissionPrompt sessionId="chat-1" />);
        fireEvent.click(screen.getByRole('button', { name }));
        await waitFor(() => expect(respond).toHaveBeenCalledExactlyOnceWith('perm-1', decision));
    });

    it('shows why the answer failed and lets the user try again', async () => {
        respond.mockRejectedValueOnce(new Error('GAIA is not reachable'));
        useNotificationStore.getState().addNotification(request());
        render(<PermissionPrompt sessionId="chat-1" />);

        fireEvent.click(screen.getByRole('button', { name: 'Allow once' }));

        expect(await screen.findByRole('alert')).toHaveTextContent('GAIA is not reachable');
        const allow = screen.getByRole('button', { name: 'Allow once' });
        await waitFor(() => expect(allow).toBeEnabled());
        fireEvent.click(allow);
        await waitFor(() => expect(respond).toHaveBeenCalledTimes(2));
    });

    it('shows the newest pending request, the one the agent is waiting on', () => {
        const add = useNotificationStore.getState().addNotification;
        add(request({ id: 'first', tool: 'write_file' }));
        add(request({ id: 'second', tool: 'delete_file' }));
        render(<PermissionPrompt sessionId="chat-1" />);
        expect(screen.getByRole('alertdialog')).toHaveTextContent('delete_file');
    });
});
