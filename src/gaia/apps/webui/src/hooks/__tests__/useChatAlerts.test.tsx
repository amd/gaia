// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Chat alerts reach the user with the panel closed (#4813, #5226). */

import { act, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatAlerts } from '../useChatAlerts';
import { useChatStore } from '../../stores/chatStore';
import { DEFAULT_ALERT_SETTINGS, useAlertSettings, useNotificationStore } from '../../stores/notificationStore';
import * as api from '../../services/api';
import type { GaiaNotification } from '../../types/agent';
import type { Session } from '../../types';

vi.mock('../../services/api');
const mockedApi = vi.mocked(api);

function Harness() {
    useChatAlerts();
    return null;
}

function chat(id: string, extra: Partial<Session> = {}): Session {
    return {
        id, title: `Chat ${id}`, created_at: '', updated_at: '', model: '', system_prompt: null,
        message_count: 0, document_ids: [], kind: 'chat', ...extra,
    };
}

type Listener = (data: unknown) => void;

function fakeElectron() {
    const listeners: Record<string, Listener[]> = {};
    const on = (channel: string) => vi.fn((cb: Listener) => {
        (listeners[channel] ??= []).push(cb);
        return () => { listeners[channel] = listeners[channel].filter((l) => l !== cb); };
    });
    const api = {
        notification: {
            onNotification: on('notification:new'),
            onPermissionRequest: on('notification:permission-request'),
            respondPermission: vi.fn(),
            show: vi.fn().mockResolvedValue(true),
            flash: vi.fn().mockResolvedValue(true),
            setAttentionCount: vi.fn().mockResolvedValue(undefined),
        },
        tray: { onNavigate: on('tray:navigate') },
        power: { setKeepAwake: vi.fn().mockResolvedValue(true) },
    };
    const emit = (channel: string, data: unknown) => (listeners[channel] ?? []).forEach((l) => l(data));
    return { api, listeners, emit };
}

let electron: ReturnType<typeof fakeElectron>;
let focused = false;

beforeEach(() => {
    electron = fakeElectron();
    window.gaiaAPI = electron.api as unknown as typeof window.gaiaAPI;
    focused = false;
    mockedApi.getSession.mockImplementation(async (id) => chat(id, { last_run_status: 'done' }));
    vi.spyOn(document, 'hasFocus').mockImplementation(() => focused);
    useAlertSettings.setState({ ...DEFAULT_ALERT_SETTINGS, sound: false });
    useNotificationStore.setState({ notifications: [], showPanel: false });
    useChatStore.setState({
        sessions: [chat('a'), chat('b')], currentSessionId: 'a',
        runningSessionIds: [], queuedSessionIds: [], awaitingBySession: {},
    });
});

afterEach(() => {
    delete window.gaiaAPI;
    vi.restoreAllMocks();
});

describe('Electron notifications (#4813)', () => {
    it('subscribes once at app level, with the panel closed, and unsubscribes on unmount', () => {
        const { unmount, rerender } = render(<Harness />);
        rerender(<Harness />);
        expect(electron.api.notification.onNotification).toHaveBeenCalledTimes(1);
        expect(electron.api.notification.onPermissionRequest).toHaveBeenCalledTimes(1);

        const n = { id: 'n1', type: 'error', title: 'Agent Crashed' } as GaiaNotification;
        act(() => electron.emit('notification:new', n));
        act(() => electron.emit('notification:permission-request', { ...n, id: 'n2', type: 'permission_request' }));
        expect(useNotificationStore.getState().notifications.map((x) => x.id)).toEqual(['n2', 'n1']);

        unmount();
        expect(electron.listeners['notification:new']).toEqual([]);
        expect(electron.listeners['notification:permission-request']).toEqual([]);
    });

    it('opens the chat whose banner was clicked', () => {
        render(<Harness />);
        act(() => electron.emit('tray:navigate', 'session:b'));
        expect(useChatStore.getState().currentSessionId).toBe('b');
    });
});

describe('chat alerts', () => {
    it('shows a banner and flashes the taskbar when a background chat needs approval', () => {
        useChatStore.setState({ runningSessionIds: ['b'] });
        render(<Harness />);
        act(() => useChatStore.setState({ awaitingBySession: { b: 'permission' } }));
        expect(electron.api.notification.show).toHaveBeenCalledWith({
            title: 'GAIA needs your approval', body: 'Chat b', sessionId: 'b',
        });
        expect(electron.api.notification.flash).toHaveBeenCalledTimes(1);
        expect(electron.api.notification.setAttentionCount).toHaveBeenLastCalledWith(1);
    });

    it('stays quiet for the chat you are looking at', () => {
        focused = true;
        useChatStore.setState({ runningSessionIds: ['a'] });
        render(<Harness />);
        act(() => useChatStore.setState({ runningSessionIds: [] }));
        expect(electron.api.notification.show).not.toHaveBeenCalled();
    });

    it('fires on finish, and each event type can be turned off on its own', async () => {
        useAlertSettings.getState().setMode('needs_approval', 'off');
        useChatStore.setState({ runningSessionIds: ['b'] });
        render(<Harness />);
        act(() => useChatStore.setState({ awaitingBySession: { b: 'permission' } }));
        expect(electron.api.notification.show).not.toHaveBeenCalled();

        act(() => useChatStore.setState({ awaitingBySession: {}, runningSessionIds: [] }));
        await waitFor(() => expect(electron.api.notification.show).toHaveBeenCalledWith(
            expect.objectContaining({ title: 'GAIA finished', sessionId: 'b' }),
        ));
    });

    it("reads the turn's own outcome for the finish banner, not the list's stale one", async () => {
        // The list still says the previous turn failed; this one succeeded.
        useChatStore.setState({ sessions: [chat('a'), chat('b', { last_run_status: 'failed' })], runningSessionIds: ['b'] });
        render(<Harness />);
        act(() => useChatStore.setState({ runningSessionIds: [] }));
        await waitFor(() => expect(electron.api.notification.show).toHaveBeenCalledWith(
            expect.objectContaining({ title: 'GAIA finished', sessionId: 'b' }),
        ));
        expect(mockedApi.getSession).toHaveBeenCalledWith('b');
        expect(useChatStore.getState().sessions.find((s) => s.id === 'b')?.last_run_status).toBe('done');

        mockedApi.getSession.mockResolvedValueOnce(chat('b', { last_run_status: 'failed' }));
        act(() => useChatStore.setState({ runningSessionIds: ['b'] }));
        act(() => useChatStore.setState({ runningSessionIds: [] }));
        await waitFor(() => expect(electron.api.notification.show).toHaveBeenLastCalledWith(
            expect.objectContaining({ title: 'GAIA hit a problem' }),
        ));
    });

    it('badge mode counts without a banner', () => {
        useAlertSettings.getState().setMode('question', 'badge');
        useChatStore.setState({ runningSessionIds: ['b'] });
        render(<Harness />);
        act(() => useChatStore.setState({ awaitingBySession: { b: 'question' } }));
        expect(electron.api.notification.show).not.toHaveBeenCalled();
        expect(electron.api.notification.flash).toHaveBeenCalledTimes(1);
        expect(electron.api.notification.setAttentionCount).toHaveBeenLastCalledWith(1);
    });

    it('never alerts for eval or scheduled runs', () => {
        useChatStore.setState({ sessions: [chat('e', { kind: 'eval' })], runningSessionIds: ['e'] });
        render(<Harness />);
        act(() => useChatStore.setState({ runningSessionIds: [] }));
        expect(electron.api.notification.show).not.toHaveBeenCalled();
    });
});

describe('keep-awake', () => {
    it('holds off sleep only while a turn runs', () => {
        render(<Harness />);
        expect(electron.api.power.setKeepAwake).toHaveBeenLastCalledWith(false);
        act(() => useChatStore.setState({ runningSessionIds: ['a'] }));
        expect(electron.api.power.setKeepAwake).toHaveBeenLastCalledWith(true);
        act(() => useChatStore.setState({ runningSessionIds: [] }));
        expect(electron.api.power.setKeepAwake).toHaveBeenLastCalledWith(false);
    });

    it('follows the setting and the battery policy', async () => {
        const battery = Object.assign(new EventTarget(), { charging: false, level: 0.5 });
        Object.defineProperty(navigator, 'getBattery', { value: () => Promise.resolve(battery), configurable: true });
        useChatStore.setState({ runningSessionIds: ['a'] });
        render(<Harness />);
        await act(async () => { await Promise.resolve(); });
        expect(electron.api.power.setKeepAwake).toHaveBeenLastCalledWith(true);

        act(() => {
            battery.level = 0.1;
            battery.dispatchEvent(new Event('levelchange'));
        });
        expect(electron.api.power.setKeepAwake).toHaveBeenLastCalledWith(false);

        act(() => useAlertSettings.getState().update({ batteryFloor: 0 }));
        expect(electron.api.power.setKeepAwake).toHaveBeenLastCalledWith(true);

        act(() => useAlertSettings.getState().update({ keepAwake: false }));
        expect(electron.api.power.setKeepAwake).toHaveBeenLastCalledWith(false);
        delete (navigator as { getBattery?: unknown }).getBattery;
    });
});
