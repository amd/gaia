// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
    ALERT_SETTINGS_KEY,
    DEFAULT_ALERT_SETTINGS,
    loadAlertSettings,
    selectPromptingSessionIds,
    useAlertSettings,
    useNotificationStore,
} from '../notificationStore';
import type { GaiaNotification } from '../../types/agent';

function prompt(id: string, sessionId: string, extra: Partial<GaiaNotification> = {}): GaiaNotification {
    return {
        id, type: 'permission_request', agentId: 'gaia', agentName: 'GAIA', title: '', message: '',
        timestamp: 0, read: false, dismissed: false, priority: 'high', sessionId, ...extra,
    };
}

beforeEach(() => localStorage.clear());

describe('selectPromptingSessionIds', () => {
    it('lists each chat with an unanswered prompt once', () => {
        useNotificationStore.setState({
            notifications: [
                prompt('1', 'b'), prompt('2', 'b'), prompt('3', 'a'),
                prompt('4', 'c', { response: 'allow' }), prompt('5', 'd', { dismissed: true }),
            ],
        });
        expect(selectPromptingSessionIds(useNotificationStore.getState())).toEqual(['a', 'b']);
    });
});

describe('alert settings', () => {
    it('defaults when nothing is saved', () => {
        expect(loadAlertSettings()).toEqual(DEFAULT_ALERT_SETTINGS);
    });

    it('turns each notification type off on its own, and keeps it across reloads', () => {
        useAlertSettings.setState(DEFAULT_ALERT_SETTINGS);
        useAlertSettings.getState().setMode('question', 'off');
        expect(useAlertSettings.getState().modes).toEqual({
            needs_approval: 'banner', question: 'off', finished: 'banner',
        });
        useAlertSettings.getState().setMode('finished', 'badge');
        expect(loadAlertSettings().modes).toEqual({
            needs_approval: 'banner', question: 'off', finished: 'badge',
        });
    });

    it('reports and drops invalid saved values', () => {
        const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
        localStorage.setItem(ALERT_SETTINGS_KEY, JSON.stringify({
            modes: { needs_approval: 'shout', finished: 'off' }, sound: 'yes', batteryFloor: 250,
        }));
        const s = loadAlertSettings();
        expect(s.modes).toEqual({ needs_approval: 'banner', question: 'banner', finished: 'off' });
        expect(s.sound).toBe(false);
        expect(s.batteryFloor).toBe(20);
        expect(warn).toHaveBeenCalledWith(expect.stringContaining('shout'));

        localStorage.setItem(ALERT_SETTINGS_KEY, '{not json');
        expect(loadAlertSettings()).toEqual(DEFAULT_ALERT_SETTINGS);
        expect(warn).toHaveBeenCalledWith(expect.stringContaining('corrupt'), expect.anything());
        warn.mockRestore();
    });
});
