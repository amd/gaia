// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PermissionPrompt } from '../PermissionPrompt';
import { useNotificationStore } from '../../stores/notificationStore';

const originalRespond = useNotificationStore.getState().respondToPermission;
const respond = vi.fn().mockResolvedValue(undefined);
beforeEach(() => {
    vi.clearAllMocks();
    useNotificationStore.setState({ notifications: [], respondToPermission: respond });
});
afterEach(() => { useNotificationStore.setState({ notifications: [], respondToPermission: originalRespond }); });

it.each(['share_engineering_context', 'append_engineering_context', 'approve_engineering_code', 'allow_path_access'])(
    'shows one-time approval without an always option for %s', async (tool) => {
        useNotificationStore.getState().addNotification({ id: 'decision', type: 'permission_request', agentId: 'session', sessionId: 'session', agentName: 'GAIA', title: 'Review selected context', message: 'Share with configured provider', timestamp: 1, read: false, dismissed: false, priority: 'high', tool, toolArgs: { context: 'private sample' }, alwaysScope: tool });
        render(<PermissionPrompt sessionId="session" />);
        expect(screen.queryByRole('button', { name: /Always allow/ })).not.toBeInTheDocument();
        expect(screen.getByText('Share with configured provider')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Details' }));
        expect(screen.getByText(/private sample/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Allow once' }));
        await waitFor(() => expect(respond).toHaveBeenCalledWith('decision', 'allow'));
    }
);
