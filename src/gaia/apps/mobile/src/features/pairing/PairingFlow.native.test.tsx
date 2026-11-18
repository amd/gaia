// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { BackHandler, Platform, StyleSheet } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { translator } from '@/i18n/useT';
import { useConnectionStore } from '@/state/connection';
import { UIProvider } from '@/ui';

import { createFakePairingService } from './fakePairingService';
import { PairingFlow } from './PairingFlow';
import { setPairingService } from './registry';

const describeNative = Platform.OS === 'web' ? describe.skip : describe;
const t = translator('en');

const wrap = ({ children }: { children: ReactNode }) => (
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
        <UIProvider>{children}</UIProvider>
    </SafeAreaProvider>
);

let backHandlers: (() => boolean)[] = [];

async function pressBack(): Promise<boolean> {
    let handled = false;
    await act(async () => {
        handled = [...backHandlers].reverse().some((handler) => handler());
    });
    return handled;
}

describeNative('PairingFlow', () => {
    beforeEach(() => {
        backHandlers = [];
        jest.spyOn(BackHandler, 'addEventListener').mockImplementation((_event, handler) => {
            backHandlers.push(handler as () => boolean);
            return { remove: () => (backHandlers = backHandlers.filter((h) => h !== handler)) };
        });
        useConnectionStore.setState({ connection: null, restored: true });
    });

    afterEach(() => {
        jest.restoreAllMocks();
        setPairingService(null);
    });

    it('goes back to the screen it was opened from at its first step, one step per press (Android back)', async () => {
        setPairingService(createFakePairingService());
        const onExit = jest.fn();
        await render(<PairingFlow initialStep="how" onExit={onExit} />, { wrapper: wrap });
        await fireEvent.press(await screen.findByText(t('mobileApp.pairing.how.paste')));
        await screen.findByTestId('pairing-input');

        expect(await pressBack()).toBe(true);
        expect(onExit).not.toHaveBeenCalled();
        expect(await pressBack()).toBe(true);
        expect(onExit).toHaveBeenCalledTimes(1);
    });

    it('leaves back unhandled while another drawer route is in front, even with an exit set', async () => {
        setPairingService(createFakePairingService());
        const onExit = jest.fn();
        await render(<PairingFlow initialStep="how" onExit={onExit} backActive={false} />, { wrapper: wrap });
        await screen.findByText(t('mobileApp.pairing.how.title'));
        expect(await pressBack()).toBe(false);
        expect(onExit).not.toHaveBeenCalled();
    });

    it('walks back one step per press, then leaves back to the system (Android back)', async () => {
        setPairingService(createFakePairingService());
        await render(<PairingFlow />, { wrapper: wrap });
        await fireEvent.press(await screen.findByText(t('mobileApp.pairing.welcome.start')));
        await fireEvent.press(await screen.findByText(t('mobileApp.pairing.how.paste')));
        await screen.findByTestId('pairing-input');

        expect(await pressBack()).toBe(true);
        expect(await screen.findByText(t('mobileApp.pairing.how.title'))).toBeTruthy();
        expect(await pressBack()).toBe(true);
        expect(await screen.findByText(t('mobileApp.pairing.welcome.title'))).toBeTruthy();
        expect(await pressBack()).toBe(false);
    });

    it('leaves back to the system at the first step when nothing opened it', async () => {
        setPairingService(createFakePairingService());
        await render(<PairingFlow initialStep="how" />, { wrapper: wrap });
        await screen.findByText(t('mobileApp.pairing.how.title'));
        expect(await pressBack()).toBe(false);
    });

    it('leaves back unhandled while another drawer route is in front', async () => {
        setPairingService(createFakePairingService());
        await render(<PairingFlow initialStep="manual" backActive={false} />, { wrapper: wrap });
        await screen.findByTestId('pairing-input');
        expect(await pressBack()).toBe(false);
        expect(screen.getByTestId('pairing-input')).toBeTruthy();
    });

    it('stretches the how-to-pair page to the gutter, not to its text', async () => {
        setPairingService(createFakePairingService());
        await render(<PairingFlow initialStep="how" />, { wrapper: wrap });
        await screen.findByText(t('mobileApp.pairing.how.step4'));
        expect(StyleSheet.flatten(screen.getByTestId('pairing-page').props.style)).toMatchObject({ width: '100%', maxWidth: 560, alignSelf: 'center' });
    });

    it('leaves the waiting screen when nothing waits for Allow any more', async () => {
        const service = createFakePairingService();
        let calls = 0;
        // Waiting when it opens; then the request is gone, as when it was canceled elsewhere.
        service.status = async () => (++calls === 1 ? { state: 'pending', pcName: 'Office PC' } : { state: 'none' });
        setPairingService(service);
        await render(<PairingFlow initialStep="how" />, { wrapper: wrap });
        await screen.findByText(t('mobileApp.pairing.waiting.title'));
        expect(await screen.findByText(t('mobileApp.pairing.refused.title'), {}, { timeout: 6000 })).toBeTruthy();
        expect(calls).toBe(2);
    });
});
