// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ALWAYS_ALLOW_TOOLS_KEY, requiresFreshConsent, useNotificationStore } from '../notificationStore';
import * as api from '../../services/api';
import type { GaiaNotification } from '../../types/agent';

vi.mock('../../services/api');

const tools = ['share_engineering_context', 'append_engineering_context', 'approve_engineering_code'];
function notification(tool: string): GaiaNotification {
    return { id: 'decision', type: 'permission_request', agentId: 'session-1', agentName: 'GAIA', title: 'Approve?', message: 'Selected scope', timestamp: 1, read: false, dismissed: false, priority: 'high', tool };
}

beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    delete window.gaiaAPI;
    useNotificationStore.getState().clearAll();
    vi.mocked(api.confirmTool).mockResolvedValue(undefined as never);
});
afterEach(() => { delete window.gaiaAPI; });

describe('engineering permissions cannot be remembered', () => {
    it.each(tools)('does not persist a forged remember request for %s', async (tool) => {
        useNotificationStore.getState().addNotification(notification(tool));
        await useNotificationStore.getState().respondToPermission('decision', 'allow', true);
        expect(api.confirmTool).toHaveBeenCalledWith('session-1', true);
        expect(localStorage.getItem(ALWAYS_ALLOW_TOOLS_KEY)).toBeNull();
        expect(useNotificationStore.getState().notifications[0].response).toBe('allow');
    });
    it.each(tools)('does not forward remember through Electron for %s', async (tool) => {
        const respondPermission = vi.fn().mockResolvedValue(undefined);
        Object.defineProperty(window, 'gaiaAPI', { configurable: true, value: { notification: { respondPermission } } });
        useNotificationStore.getState().addNotification(notification(tool));
        await useNotificationStore.getState().respondToPermission('decision', 'allow', true);
        expect(respondPermission).toHaveBeenCalledWith('decision', 'allow', false);
        expect(localStorage.getItem(ALWAYS_ALLOW_TOOLS_KEY)).toBeNull();
    });
    it('keeps existing remembered permissions for unrelated tools', async () => {
        expect(requiresFreshConsent('find_files')).toBe(false);
        useNotificationStore.getState().addNotification(notification('find_files'));
        await useNotificationStore.getState().respondToPermission('decision', 'allow', true);
        expect(JSON.parse(localStorage.getItem(ALWAYS_ALLOW_TOOLS_KEY)!)).toEqual(['find_files']);
    });
});
