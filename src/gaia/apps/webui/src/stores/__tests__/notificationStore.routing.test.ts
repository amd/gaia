// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useNotificationStore } from '../notificationStore';
import type { GaiaNotification } from '../../types/agent';

const respondPermission = vi.fn();

function request(sessionId?: string): GaiaNotification {
    return {
        id: 'permission-1',
        type: 'permission_request',
        agentId: 'agent-1',
        sessionId,
        agentName: 'GAIA',
        title: 'Allow write_file?',
        message: 'Write a file',
        timestamp: 1,
        read: false,
        dismissed: false,
        priority: 'high',
        tool: 'write_file',
    };
}

beforeEach(() => {
    useNotificationStore.setState({ notifications: [], alwaysAllowGrants: [] });
    respondPermission.mockReset().mockResolvedValue(undefined);
    vi.stubGlobal('gaiaAPI', { notification: { respondPermission } });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
        JSON.stringify({ status: 'ok', approved: true }),
        { headers: { 'content-type': 'application/json' } },
    )));
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe('permission response ownership in the desktop renderer', () => {
    it.each(['allow', 'deny'] as const)('sends chat %s to the real API client even with IPC available', async (action) => {
        useNotificationStore.getState().addNotification(request('chat-1'));
        await useNotificationStore.getState().respondToPermission('permission-1', action, true);

        expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/chat/confirm-tool', {
            method: 'POST',
            headers: { 'x-gaia-ui': '1', 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: 'chat-1', approved: action === 'allow' }),
        });
        expect(respondPermission).not.toHaveBeenCalled();
        expect(useNotificationStore.getState().notifications[0].response).toBe(action);
        expect(useNotificationStore.getState().isAlwaysAllowed('chat-1', 'write_file')).toBe(action === 'allow');
    });

    it('delivers OS-agent answers through IPC without a chat grant', async () => {
        useNotificationStore.getState().addNotification(request());
        await useNotificationStore.getState().respondToPermission('permission-1', 'allow', true);

        expect(respondPermission).toHaveBeenCalledExactlyOnceWith('permission-1', 'allow', true);
        expect(fetch).not.toHaveBeenCalled();
        expect(useNotificationStore.getState().alwaysAllowGrants).toEqual([]);
        expect(useNotificationStore.getState().notifications[0].response).toBe('allow');
    });

    it('keeps a rejected chat response actionable without granting or trying IPC', async () => {
        vi.mocked(fetch).mockResolvedValueOnce(new Response('No active chat session', { status: 404 }));
        const error = vi.spyOn(console, 'error').mockImplementation(() => {});
        useNotificationStore.getState().addNotification(request('chat-1'));
        await useNotificationStore.getState().respondToPermission('permission-1', 'allow', true);

        expect(error).toHaveBeenCalled();
        expect(respondPermission).not.toHaveBeenCalled();
        expect(useNotificationStore.getState().notifications[0].response).toBeUndefined();
        expect(useNotificationStore.getState().alwaysAllowGrants).toEqual([]);
    });

    it('keeps an OS-agent request pending when IPC is unavailable', async () => {
        vi.stubGlobal('gaiaAPI', undefined);
        const error = vi.spyOn(console, 'error').mockImplementation(() => {});
        useNotificationStore.getState().addNotification(request());
        await useNotificationStore.getState().respondToPermission('permission-1', 'allow', true);

        expect(error).toHaveBeenCalled();
        expect(fetch).not.toHaveBeenCalled();
        expect(useNotificationStore.getState().notifications[0].response).toBeUndefined();
    });
});
