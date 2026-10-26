// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionsSettings } from '../PermissionsSettings';
import { useChatStore } from '../../../stores/chatStore';
import * as api from '../../../services/api';
import type { Session, SessionPermissions } from '../../../types';

vi.mock('../../../services/api');

const mockedApi = vi.mocked(api);

const CHAT: Session = {
    id: 'chat-1', title: 'Release prep', created_at: '', updated_at: '', model: '',
    system_prompt: null, message_count: 3, document_ids: [],
};

const ROWS: SessionPermissions[] = [
    {
        session_id: 'chat-1',
        mode: 'ask',
        grants: [
            { key: 'shell:git status', label: 'git status' },
            { key: 'shell:gh issue list', label: 'gh issue list' },
        ],
    },
    { session_id: 'chat-2', mode: 'full_access', grants: [] },
];

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ sessions: [CHAT] });
    mockedApi.listAllPermissions.mockResolvedValue({ default_mode: 'ask', sessions: ROWS });
    mockedApi.revokeGrants.mockResolvedValue({ ...ROWS[0], grants: [] });
    mockedApi.setPermissionMode.mockResolvedValue({ ...ROWS[1], mode: 'ask' });
});

describe('PermissionsSettings', () => {
    it('lists each chat\'s grants under its title', async () => {
        render(<PermissionsSettings />);
        const card = await screen.findByRole('region', { name: 'Release prep' });
        expect(within(card).getByText('git status')).toBeInTheDocument();
        expect(within(card).getByText('gh issue list')).toBeInTheDocument();
        // A chat not in the sidebar list is labelled by its id.
        expect(screen.getByRole('region', { name: 'chat-2' })).toBeInTheDocument();
    });

    it('revokes one grant by its key, then reloads', async () => {
        render(<PermissionsSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Revoke git status' }));
        await waitFor(() => expect(mockedApi.revokeGrants).toHaveBeenCalledExactlyOnceWith('chat-1', 'shell:git status'));
        await waitFor(() => expect(mockedApi.listAllPermissions).toHaveBeenCalledTimes(2));
    });

    it('revokes every grant in a chat', async () => {
        render(<PermissionsSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Revoke all in this chat' }));
        await waitFor(() => expect(mockedApi.revokeGrants).toHaveBeenCalledExactlyOnceWith('chat-1'));
    });

    it('turns off full access for a chat', async () => {
        render(<PermissionsSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Turn off full access' }));
        await waitFor(() => expect(mockedApi.setPermissionMode).toHaveBeenCalledWith('chat-2', 'ask'));
    });

    it('shows why a revoke failed', async () => {
        mockedApi.revokeGrants.mockRejectedValue(new Error('Chat is gone'));
        render(<PermissionsSettings />);
        fireEvent.click(await screen.findByRole('button', { name: 'Revoke git status' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Chat is gone');
    });

    it('says when nothing is allowed', async () => {
        mockedApi.listAllPermissions.mockResolvedValue({ default_mode: 'ask', sessions: [] });
        render(<PermissionsSettings />);
        expect(await screen.findByText(/No chat has full access/)).toBeInTheDocument();
    });

    describe('default for new chats', () => {
        const TOGGLE = 'Start new chats in full access';

        beforeEach(() => {
            mockedApi.setDefaultPermissionMode.mockResolvedValue({ default_mode: 'full_access' });
        });

        it('shows the saved default', async () => {
            mockedApi.listAllPermissions.mockResolvedValue({ default_mode: 'full_access', sessions: [] });
            render(<PermissionsSettings />);
            await waitFor(() => expect(screen.getByRole('checkbox', { name: TOGGLE })).toBeChecked());
        });

        it('saves nothing until the risk is acknowledged', async () => {
            render(<PermissionsSettings />);
            const toggle = await screen.findByRole('checkbox', { name: TOGGLE });
            await waitFor(() => expect(toggle).toBeEnabled());
            fireEvent.click(toggle);
            expect(await screen.findByRole('alert')).toHaveTextContent(/without asking you first/);
            expect(toggle).not.toBeChecked();
            expect(mockedApi.setDefaultPermissionMode).not.toHaveBeenCalled();

            fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
            expect(screen.queryByRole('alert')).not.toBeInTheDocument();
            expect(mockedApi.setDefaultPermissionMode).not.toHaveBeenCalled();
        });

        it('turns it on after the explicit confirm, then reloads', async () => {
            render(<PermissionsSettings />);
            const toggle = await screen.findByRole('checkbox', { name: TOGGLE });
            await waitFor(() => expect(toggle).toBeEnabled());
            fireEvent.click(toggle);
            fireEvent.click(screen.getByRole('button', { name: 'Turn on full access for new chats' }));
            await waitFor(() => expect(mockedApi.setDefaultPermissionMode).toHaveBeenCalledExactlyOnceWith('full_access'));
            await waitFor(() => expect(mockedApi.listAllPermissions).toHaveBeenCalledTimes(2));
        });

        it('turns it off with one click', async () => {
            mockedApi.listAllPermissions.mockResolvedValue({ default_mode: 'full_access', sessions: [] });
            mockedApi.setDefaultPermissionMode.mockResolvedValue({ default_mode: 'ask' });
            render(<PermissionsSettings />);
            const toggle = await screen.findByRole('checkbox', { name: TOGGLE });
            await waitFor(() => expect(toggle).toBeChecked());
            fireEvent.click(toggle);
            await waitFor(() => expect(mockedApi.setDefaultPermissionMode).toHaveBeenCalledExactlyOnceWith('ask'));
        });

        it('a saved change moves the new-chat mode at once, both ways', async () => {
            useChatStore.setState({
                defaultPermissionMode: 'full_access', draftPermissionMode: 'full_access', draftPermissionModeChosen: false,
            });
            mockedApi.listAllPermissions.mockResolvedValue({ default_mode: 'full_access', sessions: [] });
            mockedApi.setDefaultPermissionMode.mockImplementation(async (mode) => {
                mockedApi.listAllPermissions.mockResolvedValue({ default_mode: mode, sessions: [] });
                return { default_mode: mode };
            });
            render(<PermissionsSettings />);
            const toggle = await screen.findByRole('checkbox', { name: TOGGLE });
            await waitFor(() => expect(toggle).toBeChecked());

            fireEvent.click(toggle);
            await waitFor(() => expect(useChatStore.getState().draftPermissionMode).toBe('ask'));
            expect(useChatStore.getState().defaultPermissionMode).toBe('ask');

            fireEvent.click(toggle);
            fireEvent.click(screen.getByRole('button', { name: 'Turn on full access for new chats' }));
            await waitFor(() => expect(useChatStore.getState().draftPermissionMode).toBe('full_access'));
            expect(useChatStore.getState().defaultPermissionMode).toBe('full_access');
        });

        it('shows why saving failed and stays off', async () => {
            mockedApi.setDefaultPermissionMode.mockRejectedValue(new Error('config is read-only'));
            render(<PermissionsSettings />);
            const toggle = await screen.findByRole('checkbox', { name: TOGGLE });
            await waitFor(() => expect(toggle).toBeEnabled());
            fireEvent.click(toggle);
            fireEvent.click(screen.getByRole('button', { name: 'Turn on full access for new chats' }));
            expect(await screen.findByText('config is read-only')).toBeInTheDocument();
            expect(toggle).not.toBeChecked();
        });
    });
});
