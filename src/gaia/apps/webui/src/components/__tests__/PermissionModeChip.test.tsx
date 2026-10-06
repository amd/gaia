// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionModeChip } from '../PermissionModeChip';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ draftPermissionMode: 'ask' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'chat-1', mode: 'ask', grants: [] });
    mockedApi.setPermissionMode.mockResolvedValue({ session_id: 'chat-1', mode: 'full_access', grants: [] });
});

function openMenu() {
    fireEvent.click(screen.getByRole('button', { name: /Permission mode/ }));
    return screen.getByRole('dialog', { name: 'Permission mode' });
}

describe('PermissionModeChip on an existing chat', () => {
    it('shows the chat\'s current mode', async () => {
        mockedApi.getPermissions.mockResolvedValue({ session_id: 'chat-1', mode: 'full_access', grants: [] });
        render(<PermissionModeChip sessionId="chat-1" />);
        expect(await screen.findByRole('button', { name: 'Permission mode: full access' })).toBeInTheDocument();
        expect(mockedApi.getPermissions).toHaveBeenCalledWith('chat-1');
    });

    it('needs a confirming click before turning on full access', async () => {
        render(<PermissionModeChip sessionId="chat-1" />);
        await waitFor(() => expect(mockedApi.getPermissions).toHaveBeenCalled());
        const menu = openMenu();

        fireEvent.click(within(menu).getByRole('button', { name: /Full access/ }));
        expect(mockedApi.setPermissionMode).not.toHaveBeenCalled();
        expect(within(menu).getByRole('alert')).toHaveTextContent('without asking');

        fireEvent.click(within(menu).getByRole('button', { name: 'Turn it on' }));
        await waitFor(() => expect(mockedApi.setPermissionMode).toHaveBeenCalledExactlyOnceWith('chat-1', 'full_access'));
        expect(await screen.findByRole('button', { name: 'Permission mode: full access' })).toBeInTheDocument();
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('turns full access off without a confirmation', async () => {
        mockedApi.getPermissions.mockResolvedValue({ session_id: 'chat-1', mode: 'full_access', grants: [] });
        mockedApi.setPermissionMode.mockResolvedValue({ session_id: 'chat-1', mode: 'ask', grants: [] });
        render(<PermissionModeChip sessionId="chat-1" />);
        await screen.findByRole('button', { name: 'Permission mode: full access' });
        const menu = openMenu();
        fireEvent.click(within(menu).getByRole('button', { name: /Ask before acting/ }));
        await waitFor(() => expect(mockedApi.setPermissionMode).toHaveBeenCalledWith('chat-1', 'ask'));
    });

    it('shows why the mode could not be changed', async () => {
        mockedApi.setPermissionMode.mockRejectedValue(new Error('Chat not found'));
        render(<PermissionModeChip sessionId="chat-1" />);
        await waitFor(() => expect(mockedApi.getPermissions).toHaveBeenCalled());
        const menu = openMenu();
        fireEvent.click(within(menu).getByRole('button', { name: /Full access/ }));
        fireEvent.click(within(menu).getByRole('button', { name: 'Turn it on' }));
        expect(await within(menu).findByText('Chat not found')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Permission mode: ask before acting' })).toBeInTheDocument();
    });
});

describe('PermissionModeChip before the chat exists', () => {
    it('holds the choice as the draft mode without calling the API', async () => {
        render(<PermissionModeChip sessionId={null} />);
        const menu = openMenu();
        fireEvent.click(within(menu).getByRole('button', { name: /Full access/ }));
        expect(useChatStore.getState().draftPermissionMode).toBe('ask');
        fireEvent.click(within(menu).getByRole('button', { name: 'Turn it on' }));

        await waitFor(() => expect(useChatStore.getState().draftPermissionMode).toBe('full_access'));
        expect(mockedApi.setPermissionMode).not.toHaveBeenCalled();
        expect(mockedApi.getPermissions).not.toHaveBeenCalled();
        expect(screen.getByRole('button', { name: 'Permission mode: full access' })).toBeInTheDocument();
    });
});
