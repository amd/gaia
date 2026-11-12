// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A permission card asks in the user's words: "Run this Python script?", a folder by its name. */

import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionPrompt, shortScope } from '../PermissionPrompt';
import { useNotificationStore } from '../../stores/notificationStore';
import type { GaiaNotification } from '../../types/agent';

const originalRespond = useNotificationStore.getState().respondToPermission;

function show(over: Partial<GaiaNotification>) {
    useNotificationStore.getState().addNotification({
        id: 'perm-1', type: 'permission_request', agentId: 'chat-1', sessionId: 'chat-1',
        agentName: 'GAIA', title: '', message: '', timestamp: 1, read: false, dismissed: false,
        priority: 'high', tool: 'run_python', toolArgs: { code: 'print(1)' }, ...over,
    });
    render(<PermissionPrompt sessionId="chat-1" />);
}

beforeEach(() => {
    useNotificationStore.setState({ notifications: [], respondToPermission: vi.fn().mockResolvedValue(undefined) });
});
afterEach(() => {
    useNotificationStore.setState({ notifications: [], respondToPermission: originalRespond });
});

describe('plain permission wording', () => {
    it.each([
        ['run_python', 'running python', 'Run this Python script?'],
        ['execute_python_file', 'running python file', 'Run this Python file?'],
        ['run_shell_command', 'running a shell command', 'Run this command?'],
    ])('asks about %s without the tool name', (tool, toolLabel, heading) => {
        show({ tool, toolLabel, toolArgs: { code: 'x', command: 'x', file_path: 'x.py' } });

        expect(screen.getByRole('heading')).toHaveTextContent(heading);
    });

    it('names a folder grant by the folder, with the full path on hover', () => {
        const label = 'changing files in C:\\gtest\\h18-i2\\Documents\\uxa';
        show({ tool: 'write_file', toolLabel: 'writing file', toolArgs: { file_path: 'a.md', content: 'x' }, alwaysScope: label, alwaysLabel: label });

        const always = screen.getByRole('button', { name: 'Always allow changing files in uxa in this chat' });
        expect(always).toHaveAttribute('title', label);
    });
});

describe('shortScope', () => {
    it.each([
        ['changing files in C:\\Users\\me\\OneDrive - AMD\\uxa', 'changing files in uxa'],
        ['changing files in /home/u/notes', 'changing files in notes'],
        ['git status', 'git status'],
        ['pytest (runs the test code it finds)', 'pytest (runs the test code it finds)'],
        ['Python snippets', 'Python snippets'],
    ])('%s -> %s', (scope, short) => {
        expect(shortScope(scope)).toBe(short);
    });
});
