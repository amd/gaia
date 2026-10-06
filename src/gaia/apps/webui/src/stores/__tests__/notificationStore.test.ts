// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

vi.mock('../../services/api', () => ({
    confirmTool: vi.fn(() => Promise.resolve({ status: 'ok', approved: true, granted: null })),
}));

import { confirmTool } from '../../services/api';
import {
    useNotificationStore,
    purgeLegacyAlwaysAllow,
    selectSessionPermissionPrompt,
    LEGACY_ALWAYS_ALLOW_TOOLS_KEY,
} from '../notificationStore';
import type { GaiaNotification } from '../../types/agent';

function permissionRequest(
    id: string,
    tool: string,
    sessionId?: string,
    extra: Partial<GaiaNotification> = {},
): GaiaNotification {
    return {
        id,
        type: 'permission_request',
        agentId: sessionId ?? 'os-agent',
        sessionId,
        agentName: 'GAIA',
        title: `Allow ${tool}?`,
        message: `The agent wants to execute: ${tool}`,
        timestamp: Date.now(),
        read: false,
        dismissed: false,
        priority: 'high',
        tool,
        ...extra,
    };
}

const respondPermission = vi.fn();

beforeEach(() => {
    localStorage.clear();
    useNotificationStore.setState({ notifications: [] });
    vi.mocked(confirmTool).mockClear();
    respondPermission.mockReset().mockResolvedValue(undefined);
    delete window.gaiaAPI;
});
afterEach(() => { delete window.gaiaAPI; });

function withIpc() {
    Object.defineProperty(window, 'gaiaAPI', {
        configurable: true,
        value: { notification: { respondPermission } },
    });
}

describe('respondToPermission for chat prompts', () => {
    it('allow once sends approved=true, always=false with the confirm id', async () => {
        useNotificationStore.getState().addNotification(
            permissionRequest('p1', 'run_shell_command', 'chat-A', { confirmId: 'c-1' }),
        );
        await useNotificationStore.getState().respondToPermission('p1', 'allow');
        expect(confirmTool).toHaveBeenCalledExactlyOnceWith('chat-A', true, { always: false, confirmId: 'c-1' });
        expect(useNotificationStore.getState().notifications[0].response).toBe('allow');
    });

    it('always sends always=true and records an allow', async () => {
        useNotificationStore.getState().addNotification(
            permissionRequest('p1', 'run_shell_command', 'chat-A', { confirmId: 'c-1', alwaysScope: 'git status' }),
        );
        await useNotificationStore.getState().respondToPermission('p1', 'always');
        expect(confirmTool).toHaveBeenCalledExactlyOnceWith('chat-A', true, { always: true, confirmId: 'c-1' });
        expect(useNotificationStore.getState().notifications[0].response).toBe('allow');
    });

    it('deny sends approved=false', async () => {
        useNotificationStore.getState().addNotification(permissionRequest('p1', 'delete_file', 'chat-A'));
        await useNotificationStore.getState().respondToPermission('p1', 'deny');
        expect(confirmTool).toHaveBeenCalledExactlyOnceWith('chat-A', false, { always: false, confirmId: undefined });
        expect(useNotificationStore.getState().notifications[0].response).toBe('deny');
    });

    it('goes to the API, not IPC, even inside Electron', async () => {
        withIpc();
        useNotificationStore.getState().addNotification(permissionRequest('p1', 'write_file', 'chat-A'));
        await useNotificationStore.getState().respondToPermission('p1', 'allow');
        expect(confirmTool).toHaveBeenCalledOnce();
        expect(respondPermission).not.toHaveBeenCalled();
    });

    it('throws and stays pending when the answer does not reach the agent', async () => {
        vi.mocked(confirmTool).mockRejectedValueOnce(new Error('offline'));
        useNotificationStore.getState().addNotification(permissionRequest('p1', 'write_file', 'chat-A'));
        await expect(useNotificationStore.getState().respondToPermission('p1', 'allow')).rejects.toThrow('offline');
        expect(useNotificationStore.getState().notifications[0].response).toBeUndefined();
        expect(selectSessionPermissionPrompt('chat-A')(useNotificationStore.getState())?.id).toBe('p1');
    });

    it('throws for an unknown request id', async () => {
        await expect(useNotificationStore.getState().respondToPermission('nope', 'allow')).rejects.toThrow(/no longer pending/);
        expect(confirmTool).not.toHaveBeenCalled();
    });
});

describe('respondToPermission for OS-agent prompts', () => {
    it('delivers through IPC, forwarding always', async () => {
        withIpc();
        useNotificationStore.getState().addNotification(permissionRequest('p1', 'read_file'));
        await useNotificationStore.getState().respondToPermission('p1', 'always');
        expect(respondPermission).toHaveBeenCalledExactlyOnceWith('p1', 'allow', true);
        expect(confirmTool).not.toHaveBeenCalled();
        expect(useNotificationStore.getState().notifications[0].response).toBe('allow');
    });

    it('throws and stays pending with no IPC bridge', async () => {
        useNotificationStore.getState().addNotification(permissionRequest('p1', 'read_file'));
        await expect(useNotificationStore.getState().respondToPermission('p1', 'allow')).rejects.toThrow();
        expect(confirmTool).not.toHaveBeenCalled();
        expect(useNotificationStore.getState().notifications[0].response).toBeUndefined();
    });

    it('throws and stays pending when IPC rejects', async () => {
        withIpc();
        respondPermission.mockRejectedValueOnce(new Error('main process gone'));
        useNotificationStore.getState().addNotification(permissionRequest('p1', 'read_file'));
        await expect(useNotificationStore.getState().respondToPermission('p1', 'deny')).rejects.toThrow('main process gone');
        expect(useNotificationStore.getState().notifications[0].response).toBeUndefined();
    });
});

describe('selectSessionPermissionPrompt', () => {
    it('returns the newest pending prompt for that chat only', () => {
        const add = useNotificationStore.getState().addNotification;
        add(permissionRequest('old', 'write_file', 'chat-A'));
        add(permissionRequest('other', 'write_file', 'chat-B'));
        add(permissionRequest('new', 'write_file', 'chat-A'));
        const state = useNotificationStore.getState();
        expect(selectSessionPermissionPrompt('chat-A')(state)?.id).toBe('new');
        expect(selectSessionPermissionPrompt('chat-B')(state)?.id).toBe('other');
        expect(selectSessionPermissionPrompt('chat-C')(state)).toBeNull();
    });

    it('skips answered and dismissed prompts', () => {
        const add = useNotificationStore.getState().addNotification;
        add(permissionRequest('answered', 'write_file', 'chat-A', { response: 'allow' }));
        add(permissionRequest('dismissed', 'write_file', 'chat-A', { dismissed: true }));
        expect(selectSessionPermissionPrompt('chat-A')(useNotificationStore.getState())).toBeNull();
    });
});

describe('purgeLegacyAlwaysAllow', () => {
    it('deletes a list persisted by an earlier build and says so', () => {
        localStorage.setItem(LEGACY_ALWAYS_ALLOW_TOOLS_KEY, '["run_shell_command"]');
        const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

        purgeLegacyAlwaysAllow();

        expect(localStorage.getItem(LEGACY_ALWAYS_ALLOW_TOOLS_KEY)).toBeNull();
        expect(warnSpy).toHaveBeenCalledOnce();
        warnSpy.mockRestore();
    });

    it('is silent when there is nothing to purge', () => {
        const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
        purgeLegacyAlwaysAllow();
        expect(warnSpy).not.toHaveBeenCalled();
        warnSpy.mockRestore();
    });
});
