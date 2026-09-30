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
    mockedApi.listAllPermissions.mockResolvedValue({ sessions: ROWS });
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
        mockedApi.listAllPermissions.mockResolvedValue({ sessions: [] });
        render(<PermissionsSettings />);
        expect(await screen.findByText(/No chat has full access/)).toBeInTheDocument();
    });
});
