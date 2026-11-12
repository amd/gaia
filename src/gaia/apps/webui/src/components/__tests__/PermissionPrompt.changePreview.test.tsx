// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A write or edit prompt shows what will land in the file, not escaped JSON. */

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionPrompt } from '../PermissionPrompt';
import { editLines } from '../PermissionChangePreview';
import { useNotificationStore } from '../../stores/notificationStore';
import type { GaiaNotification } from '../../types/agent';

const originalRespond = useNotificationStore.getState().respondToPermission;

function request(tool: string, toolArgs: Record<string, unknown>): GaiaNotification {
    return {
        id: 'perm-1', type: 'permission_request', agentId: 'chat-1', sessionId: 'chat-1',
        agentName: 'GAIA', title: `Allow ${tool}?`, message: '', timestamp: 1,
        read: false, dismissed: false, priority: 'high', tool, toolArgs,
    };
}

beforeEach(() => {
    useNotificationStore.setState({ notifications: [], respondToPermission: vi.fn().mockResolvedValue(undefined) });
});
afterEach(() => {
    useNotificationStore.setState({ notifications: [], respondToPermission: originalRespond });
});

const show = (tool: string, args: Record<string, unknown>) => {
    useNotificationStore.getState().addNotification(request(tool, args));
    return render(<PermissionPrompt sessionId="chat-1" />);
};

describe('write and edit prompts', () => {
    it('shows a write as the text it will save, with real line breaks', () => {
        show('write_file', { file_path: 'C:\\docs\\summary.md', content: '# Expenses\n\n| Month | Total |\n| Oct | 40 |\n' });

        expect(screen.getByText('What will be saved')).toBeInTheDocument();
        expect(screen.getByTestId('perm-write-preview').textContent).toBe('# Expenses\n\n| Month | Total |\n| Oct | 40 |');
    });

    it('shows an edit as the lines it removes and adds', () => {
        show('edit_file', { file_path: 'C:\\docs\\notes.txt', old_content: '- owner: Dana\n', new_content: '- owner: Dana\nreviewed\n' });

        expect(screen.getByText('What will change')).toBeInTheDocument();
        const diff = screen.getByTestId('perm-edit-diff');
        expect(diff.querySelector('.render-diff__line--added')?.textContent).toBe('+reviewed');
        expect(diff.querySelector('.render-diff__line--removed')).toBeNull();
    });

    it('keeps the raw arguments behind a secondary toggle', () => {
        show('write_file', { file_path: 'a.md', content: 'hi\nthere' });

        expect(screen.queryByText(/"content"/)).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Technical details' }));
        expect(screen.getByText(/"content"/)).toBeInTheDocument();
    });

    it('folds a long write after twenty lines', () => {
        const content = Array.from({ length: 30 }, (_, i) => `line ${i + 1}`).join('\n');
        show('write_file', { file_path: 'a.md', content });

        expect(screen.getByTestId('perm-write-preview').textContent).not.toContain('line 21');
        fireEvent.click(screen.getByRole('button', { name: 'Show all 30 lines' }));
        expect(screen.getByTestId('perm-write-preview').textContent).toContain('line 30');
    });

    it('leaves other prompts with their plain Details toggle', () => {
        show('run_shell_command', { command: 'git status' });

        expect(screen.queryByText('What will be saved')).toBeNull();
        expect(screen.getByRole('button', { name: 'Details' })).toBeInTheDocument();
    });
});

describe('editLines', () => {
    it('keeps two unchanged lines around the change', () => {
        expect(editLines('a\nb\nc\nd\ne', 'a\nb\nc\nX\ne')).toEqual([' b', ' c', '-d', '+X', ' e']);
    });

    it('reads a new file as all added', () => {
        expect(editLines('', 'one\ntwo')).toEqual(['+one', '+two']);
    });
});
