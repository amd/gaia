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
