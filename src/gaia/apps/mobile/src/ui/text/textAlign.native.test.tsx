// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { I18nManager, Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { useLocaleStore } from '@/state/locale';
import { Text, ThemeProvider } from '@/ui';

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const wrap = ({ children }: { children: ReactNode }) => (
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
        <ThemeProvider>{children}</ThemeProvider>
    </SafeAreaProvider>
);

function nativeLayout(isRTL: boolean, swap: boolean) {
    jest.spyOn(I18nManager, 'getConstants').mockReturnValue({ isRTL, doLeftAndRightSwapInRTL: swap, localeIdentifier: 'ar' });
}

async function styleOf(props: Record<string, unknown>) {
    await render(<Text testID="t" {...props}>نص</Text>, { wrapper: wrap });
    await waitFor(() => expect(screen.getAllByTestId('t')).toHaveLength(1));
    return screen.getAllByTestId('t')[0].props.style;
}

beforeEach(() => useLocaleStore.getState().setLocale('ar'));
afterEach(() => {
    useLocaleStore.getState().setLocale(null);
    jest.restoreAllMocks();
});

describeNative('Text alignment in the Arabic UI', () => {
    it('passes the mirrored side when native flips left and right', async () => {
        nativeLayout(true, true);
        expect(await styleOf({})).toMatchObject({ textAlign: 'left' });
    });

    it('passes the end side mirrored, and leaves center alone', async () => {
        nativeLayout(true, true);
        expect(await styleOf({ align: 'end' })).toMatchObject({ textAlign: 'right' });
    });

    it('keeps center', async () => {
        nativeLayout(true, true);
        expect(await styleOf({ align: 'center' })).toMatchObject({ textAlign: 'center' });
    });

    it('passes the physical side when the swap is off', async () => {
        nativeLayout(true, false);
        expect(await styleOf({})).toMatchObject({ textAlign: 'right' });
        expect(await styleOf({ align: 'end' })).toMatchObject({ textAlign: 'left' });
    });

    it('passes the physical side before a restart has made the layout RTL', async () => {
        nativeLayout(false, true);
        expect(await styleOf({})).toMatchObject({ textAlign: 'right' });
    });

    it('sets no writing direction by default', async () => {
        nativeLayout(true, true);
        expect((await styleOf({})).writingDirection).toBeUndefined();
    });
});
