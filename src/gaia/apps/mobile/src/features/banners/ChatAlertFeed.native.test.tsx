// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render } from '@testing-library/react-native';
import { AppState, Platform } from 'react-native';

import { DEFAULT_ALERT_SETTINGS } from '@webui/utils/alertSettings.core';

import { useAlertSettings } from '@/state/alertSettings';
import { useChatAlertBannerStore } from '@/state/chatAlertBanner';
import { IDLE_TURN, useChatStreamStore, type ChatTurn, type PermissionRequest } from '@/state/chatStreams';

import { ChatAlertFeed } from './ChatAlertFeed';
import { FINISHED_ALERT_MS } from './chatAlerts';

let mockPathname = '/chat/b';
const mockSound = jest.fn();
jest.mock('expo-router', () => ({ usePathname: () => mockPathname }));
jest.mock('@/data', () => ({ useSessions: () => ({ data: { sessions: [{ id: 'a', title: 'Quarterly report' }, { id: 'b', title: 'Trip' }] } }) }));
jest.mock('@/platform/alertSound', () => ({ playAlertSound: () => mockSound() }));

const describeNative = Platform.OS === 'web' ? describe.skip : describe;
const running: ChatTurn = { ...IDLE_TURN, status: 'streaming' };
const asking: ChatTurn = { ...running, pendingPermission: { type: 'permission_request', tool: 'run_python', confirm_id: 'c1' } as PermissionRequest };

describeNative('ChatAlertFeed', () => {
    beforeEach(() => {
        Object.defineProperty(AppState, 'currentState', { value: 'active', configurable: true });
        mockSound.mockClear();
        useChatAlertBannerStore.setState({ alert: null });
        useAlertSettings.setState({ ...DEFAULT_ALERT_SETTINGS, hydrated: false, sound: true });
        useChatStreamStore.setState({ turns: { a: running, b: running } });
    });

    it('banners an approval in another chat with its title and sound, and clears it once answered', async () => {
        await render(<ChatAlertFeed />);
        await act(async () => useChatStreamStore.setState({ turns: { a: asking, b: running } }));
        expect(useChatAlertBannerStore.getState().alert).toMatchObject({ sessionId: 'a', event: 'needs_approval', title: 'GAIA needs your approval', message: 'Quarterly report' });
        expect(mockSound).toHaveBeenCalledTimes(1);

        await act(async () => useChatStreamStore.setState({ turns: { a: running, b: running } }));
        expect(useChatAlertBannerStore.getState().alert).toBeNull();
    });

    it('stays quiet for the chat in view and for an event set to Off', async () => {
        await render(<ChatAlertFeed />);
        await act(async () => useChatStreamStore.setState({ turns: { a: running, b: asking } }));
        expect(useChatAlertBannerStore.getState().alert).toBeNull();

        useAlertSettings.getState().setMode('finished', 'off');
        await act(async () => useChatStreamStore.setState({ turns: { b: asking } }));
        expect(useChatAlertBannerStore.getState().alert).toBeNull();
        expect(mockSound).not.toHaveBeenCalled();
    });

    it('lets a finished banner go on its own but keeps an approval until it is answered', async () => {
        jest.useFakeTimers();
        try {
            await render(<ChatAlertFeed />);
            await act(async () => useChatAlertBannerStore.getState().show({ sessionId: 'a', event: 'finished', tone: 'success', title: 'Done', message: 'Trip' }));
            await act(async () => jest.advanceTimersByTime(FINISHED_ALERT_MS));
            expect(useChatAlertBannerStore.getState().alert).toBeNull();

            await act(async () => useChatAlertBannerStore.getState().show({ sessionId: 'a', event: 'needs_approval', tone: 'warning', title: 'Approve', message: 'Trip' }));
            await act(async () => jest.advanceTimersByTime(FINISHED_ALERT_MS * 5));
            expect(useChatAlertBannerStore.getState().alert).not.toBeNull();
        } finally {
            jest.useRealTimers();
        }
    });
});
