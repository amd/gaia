// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { requiresFreshConsent, useNotificationStore } from '../notificationStore';
import * as api from '../../services/api';
import type { GaiaNotification } from '../../types/agent';

vi.mock('../../services/api');

const tools = ['share_engineering_context', 'append_engineering_context', 'approve_engineering_code'];
function notification(tool: string, sessionId?: string): GaiaNotification {
    return { id: 'decision', type: 'permission_request', agentId: 'session-1', sessionId, agentName: 'GAIA', title: 'Approve?', message: 'Selected scope', timestamp: 1, read: false, dismissed: false, priority: 'high', tool, confirmId: 'c-1', alwaysScope: tool };
}

beforeEach(() => {
    vi.clearAllMocks();
    delete window.gaiaAPI;
    useNotificationStore.getState().clearAll();
    vi.mocked(api.confirmTool).mockResolvedValue({ status: 'ok', approved: true, granted: null });
});
afterEach(() => { delete window.gaiaAPI; });

describe('engineering permissions cannot be remembered', () => {
    it.each(tools)('never sends always=true to the chat API for %s', async (tool) => {
        expect(requiresFreshConsent(tool)).toBe(true);
        useNotificationStore.getState().addNotification(notification(tool, 'session-1'));
        await useNotificationStore.getState().respondToPermission('decision', 'always');
        expect(api.confirmTool).toHaveBeenCalledWith('session-1', true, { always: false, confirmId: 'c-1' });
        expect(useNotificationStore.getState().notifications[0].response).toBe('allow');
    });

    it.each(tools)('never forwards always through Electron for %s', async (tool) => {
        const respondPermission = vi.fn().mockResolvedValue(undefined);
        Object.defineProperty(window, 'gaiaAPI', { configurable: true, value: { notification: { respondPermission } } });
        useNotificationStore.getState().addNotification(notification(tool));
        await useNotificationStore.getState().respondToPermission('decision', 'always');
        expect(respondPermission).toHaveBeenCalledWith('decision', 'allow', false);
    });

    it('still sends always for an ordinary tool', async () => {
        expect(requiresFreshConsent('find_files')).toBe(false);
        useNotificationStore.getState().addNotification(notification('find_files', 'session-1'));
        await useNotificationStore.getState().respondToPermission('decision', 'always');
        expect(api.confirmTool).toHaveBeenCalledWith('session-1', true, { always: true, confirmId: 'c-1' });
    });
});
