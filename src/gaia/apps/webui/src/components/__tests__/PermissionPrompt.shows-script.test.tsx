// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// "Allow running python file?" names a file, and a path says nothing about what
// the file does. The card shows the script as the run_python card shows its
// code, counts the lines it was not given, and says so when there is none to show.

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionPrompt } from '../PermissionPrompt';
import { useNotificationStore } from '../../stores/notificationStore';
import { useChatStore } from '../../stores/chatStore';
import type { GaiaNotification } from '../../types/agent';

const originalRespond = useNotificationStore.getState().respondToPermission;
const PATH = 'C:\\Users\\me\\AppData\\Local\\Temp\\gaia-scratch-e521\\build_recon.py';

function show(script: unknown, tool = 'execute_python_file') {
    const request: GaiaNotification = {
        id: 'perm-1', type: 'permission_request', agentId: 'chat-1', sessionId: 'chat-1',
        agentName: 'GAIA', title: 'Allow running python file?', message: '', timestamp: 1,
        read: false, dismissed: false, priority: 'high', tool,
        toolArgs: script === undefined ? { file_path: PATH } : { file_path: PATH, _script: script },
    };
    useNotificationStore.getState().addNotification(request);
    render(<PermissionPrompt sessionId="chat-1" />);
    return screen.getByRole('alertdialog');
}

beforeEach(() => {
    useNotificationStore.setState({ notifications: [], respondToPermission: vi.fn() });
    useChatStore.setState({ locale: 'en' });
});
afterEach(() => {
    useNotificationStore.setState({ notifications: [], respondToPermission: originalRespond });
    useChatStore.setState({ locale: 'en' });
});

describe('the card that asks to run a Python file', () => {
    it('shows the script under its path', () => {
        const card = show({ text: 'import shutil\nshutil.rmtree("out")', hidden_lines: 0 });
        expect(card).toHaveTextContent('build_recon.py');
        expect(screen.getByTestId('perm-code').textContent).toBe('import shutil\nshutil.rmtree("out")');
        expect(screen.queryByTestId('perm-script-more')).toBeNull();
        expect(screen.queryByTestId('perm-script-unreadable')).toBeNull();
    });

    it('collapses a long script like run_python code, with all of it one click away', () => {
        const lines = Array.from({ length: 40 }, (_, i) => `step_${i}()`);
        show({ text: lines.join('\n'), hidden_lines: 0 });
        expect(screen.getByTestId('perm-code').textContent).not.toContain('step_39()');
        fireEvent.click(screen.getByRole('button', { name: 'Show all 40 lines' }));
        expect(screen.getByTestId('perm-code').textContent).toContain('step_39()');
    });

    it('says how many lines of a capped script it was not given', () => {
        show({ text: 'a = 1\nb = 2', hidden_lines: 250 });
        expect(screen.getByTestId('perm-script-more')).toHaveTextContent(
            '250 more lines of this file are not shown. Open the file to read them.',
        );
    });

    it('shows a hidden character in the script as a code, with the note', () => {
        show({ text: 'run("rm\u202E -rf")', hidden_lines: 0 });
        const code = screen.getByTestId('perm-code').textContent ?? '';
        expect(code).toContain('rm[U+202E] -rf');
        expect(code).not.toContain('\u202E');
        expect(screen.getByTestId('perm-hidden-note')).toBeInTheDocument();
    });

    it.each([
        ['missing', "This file doesn't exist, so there is no code to show."],
        ['not_text', "This file isn't text, so its code can't be shown."],
        ['too_large', 'This file is too large to show here. Open it to read what will run.'],
        ['unreadable', "GAIA couldn't read this file, so its code isn't shown."],
        ['a reason this build has not heard of', "GAIA couldn't read this file, so its code isn't shown."],
    ])('says why when the script is %s, never an empty card', (unreadable, said) => {
        show({ unreadable });
        expect(screen.getByTestId('perm-script-unreadable')).toHaveTextContent(said);
        expect(screen.queryByTestId('perm-code')).toBeNull();
    });

    it('says an empty script is empty', () => {
        show({ text: '\n', hidden_lines: 0 });
        expect(screen.getByTestId('perm-script-empty')).toHaveTextContent('This file is empty.');
        expect(screen.queryByTestId('perm-code')).toBeNull();
    });

    it('keeps the script out of the raw details, where it would be shown twice', () => {
        const card = show({ text: 'print("once")', hidden_lines: 0 });
        fireEvent.click(screen.getByRole('button', { name: 'Details' }));
        expect(card.textContent?.split('print("once")').length).toBe(2);
        expect(card).toHaveTextContent('"file_path"');
    });

    it('takes a script only from GAIA, on the tool that runs a file', () => {
        show({ text: 'print("not for this tool")', hidden_lines: 0 }, 'write_file');
        expect(screen.queryByTestId('perm-code')).toBeNull();
    });

    it('says it in Arabic', () => {
        useChatStore.setState({ locale: 'ar' });
        show({ text: 'a = 1', hidden_lines: 3 });
        expect(screen.getByTestId('perm-script-more')).toHaveTextContent('عدد الأسطر غير المعروضة من هذا الملف: 3.');
    });
});
