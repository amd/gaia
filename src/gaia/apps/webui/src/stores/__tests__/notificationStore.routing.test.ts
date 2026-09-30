// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Uses the real API client so the wire shape of the answer is pinned.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useNotificationStore } from '../notificationStore';
import type { GaiaNotification } from '../../types/agent';

function request(sessionId: string, confirmId?: string): GaiaNotification {
    return {
        id: 'permission-1',
        type: 'permission_request',
        agentId: 'agent-1',
        sessionId,
        confirmId,
        alwaysScope: 'write_file',
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
    useNotificationStore.setState({ notifications: [] });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
        JSON.stringify({ status: 'ok', approved: true, granted: null }),
        { headers: { 'content-type': 'application/json' } },
    )));
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe('chat permission answers on the wire', () => {
    it.each([
        ['allow', true, false],
        ['always', true, true],
        ['deny', false, false],
    ] as const)('%s posts approved=%s always=%s with the confirm id', async (decision, approved, always) => {
        useNotificationStore.getState().addNotification(request('chat-1', 'confirm-9'));
        await useNotificationStore.getState().respondToPermission('permission-1', decision);

        expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/chat/confirm-tool', {
            method: 'POST',
            headers: { 'x-gaia-ui': '1', 'x-gaia-client': 'agent-ui', 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: 'chat-1', approved, always, confirm_id: 'confirm-9' }),
        });
    });

    it('omits confirm_id when the prompt carried none', async () => {
        useNotificationStore.getState().addNotification(request('chat-1'));
        await useNotificationStore.getState().respondToPermission('permission-1', 'allow');
        const body = JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string);
        expect(body).toEqual({ session_id: 'chat-1', approved: true, always: false });
    });

    it('rejects and keeps the prompt pending on an HTTP error', async () => {
        vi.mocked(fetch).mockResolvedValueOnce(new Response('No active chat session', { status: 404 }));
        vi.spyOn(console, 'error').mockImplementation(() => {});
        useNotificationStore.getState().addNotification(request('chat-1'));
        await expect(useNotificationStore.getState().respondToPermission('permission-1', 'allow')).rejects.toThrow();
        expect(useNotificationStore.getState().notifications[0].response).toBeUndefined();
    });
});
