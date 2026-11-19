// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { isolate } from '@webui/i18n/registry';

import { useLocaleStore } from '@/state/locale';
import { Text, ThemeProvider } from '@/ui';

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const wrap = ({ children }: { children: ReactNode }) => (
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
        <ThemeProvider>{children}</ThemeProvider>
    </SafeAreaProvider>
);

async function styleOf(children: ReactNode) {
    await render(<Text testID="t">{children}</Text>, { wrapper: wrap });
    await waitFor(() => expect(screen.getAllByTestId('t')).toHaveLength(1));
    return screen.getAllByTestId('t')[0].props.style;
}

afterEach(() => useLocaleStore.getState().setLocale(null));

describeNative('Text with an isolated value before the first letter', () => {
    it('pins an Arabic line to RTL, for a string or an array of strings', async () => {
        useLocaleStore.getState().setLocale('ar');
        const line = `${isolate('469.7 KB', 'ltr')} · قبل 5 د`;
        expect(await styleOf(line)).toMatchObject({ writingDirection: 'rtl' });
        expect(await styleOf([isolate('469.7 KB', 'ltr'), ' · قبل 5 د'])).toMatchObject({ writingDirection: 'rtl' });
    });

    it('leaves plain English text and an unmisled line alone', async () => {
        useLocaleStore.getState().setLocale('en');
        expect((await styleOf('469.7 KB · 5 min ago')).writingDirection).toBeUndefined();
        expect((await styleOf(`${isolate('469.7 KB', 'ltr')} · 5 min ago`)).writingDirection).toBeUndefined();
    });
});
