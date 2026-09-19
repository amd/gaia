// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('../../services/api', () => ({
    confirmTool: vi.fn(() => Promise.resolve()),
}));

import { confirmTool } from '../../services/api';
import {
    useNotificationStore,
    purgeLegacyAlwaysAllow,
    LEGACY_ALWAYS_ALLOW_TOOLS_KEY,
} from '../notificationStore';
import type { GaiaNotification } from '../../types/agent';

function permissionRequest(id: string, tool: string, sessionId?: string): GaiaNotification {
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
    };
}

async function allow(id: string, tool: string, sessionId: string | undefined, remember: boolean) {
    useNotificationStore.getState().addNotification(permissionRequest(id, tool, sessionId));
    await useNotificationStore.getState().respondToPermission(id, 'allow', remember);
}

describe('always-allow grants are scoped to one chat session', () => {
    beforeEach(() => {
        localStorage.clear();
        useNotificationStore.setState({ notifications: [], alwaysAllowGrants: [] });
        vi.mocked(confirmTool).mockClear();
    });

    it('grants the tool in the chat that asked, and only there', async () => {
        await allow('p1', 'run_shell_command', 'chat-A', true);

        const { isAlwaysAllowed } = useNotificationStore.getState();
        expect(isAlwaysAllowed('chat-A', 'run_shell_command')).toBe(true);
        expect(isAlwaysAllowed('chat-B', 'run_shell_command')).toBe(false);
        expect(isAlwaysAllowed('chat-A', 'write_file')).toBe(false);
    });

    it('never writes a grant to localStorage', async () => {
        await allow('p1', 'run_shell_command', 'chat-A', true);
        expect(localStorage.length).toBe(0);
    });

    it('a remember=false allow creates no grant', async () => {
        await allow('p1', 'write_file', 'chat-A', false);
        expect(useNotificationStore.getState().alwaysAllowGrants).toEqual([]);
        expect(useNotificationStore.getState().isAlwaysAllowed('chat-A', 'write_file')).toBe(false);
    });

    it('a deny with remember ticked creates no grant', async () => {
        useNotificationStore.getState().addNotification(permissionRequest('p1', 'delete_file', 'chat-A'));
        await useNotificationStore.getState().respondToPermission('p1', 'deny', true);
        expect(useNotificationStore.getState().alwaysAllowGrants).toEqual([]);
    });

    it('a request with no chat session (an OS agent) creates no renderer grant', async () => {
        await allow('p1', 'read_file', undefined, true);
        expect(useNotificationStore.getState().alwaysAllowGrants).toEqual([]);
    });

    it('does not grant when the answer never reached the agent', async () => {
        vi.mocked(confirmTool).mockRejectedValueOnce(new Error('offline'));
        const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
        await allow('p1', 'run_shell_command', 'chat-A', true);
        expect(useNotificationStore.getState().alwaysAllowGrants).toEqual([]);
        errorSpy.mockRestore();
    });

    it('granting twice keeps one grant', async () => {
        await allow('p1', 'web_search', 'chat-A', true);
        await allow('p2', 'web_search', 'chat-A', true);
        expect(useNotificationStore.getState().alwaysAllowGrants).toHaveLength(1);
    });

    it('revoking removes exactly that grant; Revoke All removes the rest', async () => {
        await allow('p1', 'run_shell_command', 'chat-A', true);
        await allow('p2', 'run_shell_command', 'chat-B', true);
        await allow('p3', 'web_search', 'chat-A', true);

        useNotificationStore.getState().revokeAlwaysAllow('chat-A', 'run_shell_command');
        const s = useNotificationStore.getState();
        expect(s.isAlwaysAllowed('chat-A', 'run_shell_command')).toBe(false);
        expect(s.isAlwaysAllowed('chat-B', 'run_shell_command')).toBe(true);
        expect(s.isAlwaysAllowed('chat-A', 'web_search')).toBe(true);

        useNotificationStore.getState().revokeAllAlwaysAllow();
        expect(useNotificationStore.getState().alwaysAllowGrants).toEqual([]);
    });
});

describe('grants do not survive a reload', () => {
    it('a freshly loaded store (page reload / restart) starts with no grants', async () => {
        localStorage.clear();
        await allow('p1', 'run_shell_command', 'chat-A', true);
        expect(useNotificationStore.getState().alwaysAllowGrants).toHaveLength(1);

        vi.resetModules();
        const reloaded = await import('../notificationStore');
        expect(reloaded.useNotificationStore.getState().alwaysAllowGrants).toEqual([]);
        expect(
            reloaded.useNotificationStore.getState().isAlwaysAllowed('chat-A', 'run_shell_command')
        ).toBe(false);
    });
});

describe('purgeLegacyAlwaysAllow', () => {
    beforeEach(() => localStorage.clear());

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
