// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionModeChip } from '../PermissionModeChip';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
import { readsAs } from '../../test/readsAs';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ draftPermissionMode: 'ask' });
    useNotificationStore.setState({ notifications: [] });
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

    it('says the question already waiting still needs an answer after switching to full access', async () => {
        useNotificationStore.setState({
            notifications: [{
                id: 'n-1', type: 'permission_request', agentId: 'gaia', agentName: 'GAIA', title: 'Allow?', message: '',
                timestamp: 0, read: false, dismissed: false, priority: 'high', tool: 'run_shell_command', sessionId: 'chat-1', confirmId: 'c-1',
            }],
        });
        render(<PermissionModeChip sessionId="chat-1" />);
        await waitFor(() => expect(mockedApi.getPermissions).toHaveBeenCalled());
        const menu = openMenu();
        fireEvent.click(within(menu).getByRole('button', { name: /Full access/ }));
        fireEvent.click(within(menu).getByRole('button', { name: 'Turn it on' }));

        expect(await within(menu).findByRole('status')).toHaveTextContent('still needs your answer');
        expect(screen.getByRole('dialog', { name: 'Permission mode' })).toBeInTheDocument();
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

    it('shows the saved mode when a running agent could not be told', async () => {
        mockedApi.getPermissions.mockResolvedValueOnce({ session_id: 'chat-1', mode: 'full_access', grants: [] });
        render(<PermissionModeChip sessionId="chat-1" />);
        await screen.findByRole('button', { name: 'Permission mode: full access' });
        mockedApi.setPermissionMode.mockRejectedValue(new Error('Ask is saved, but the running agent couldn\'t be told'));
        mockedApi.getPermissions.mockResolvedValue({ session_id: 'chat-1', mode: 'ask', grants: [] });
        const menu = openMenu();
        fireEvent.click(within(menu).getByRole('button', { name: /Ask before acting/ }));
        expect(await within(menu).findByText(/Ask is saved/)).toBeInTheDocument();
        expect(await screen.findByRole('button', { name: 'Permission mode: ask before acting' })).toBeInTheDocument();
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

describe('PermissionModeChip in Arabic', () => {
    afterEach(() => act(() => useChatStore.getState().setLocale('en')));

    it('labels the ask mode with a verb, not "the question"', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        render(<PermissionModeChip sessionId={null} />);
        expect(screen.getByText(readsAs('اسأل (Ask)'))).toBeInTheDocument();
    });
});
