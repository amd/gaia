// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Dimensions, Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { Row, Text, ThemeProvider } from '@/ui';

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const wrap = ({ children }: { children: ReactNode }) => (
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
        <ThemeProvider>{children}</ThemeProvider>
    </SafeAreaProvider>
);

function atFontScale(scale: number) {
    const window = Dimensions.get('window');
    jest.spyOn(Dimensions, 'get').mockImplementation(() => ({ ...window, fontScale: scale }));
}

afterEach(() => jest.restoreAllMocks());

describeNative('text at the largest iOS sizes', () => {
    it('lets a one-line text wrap, but keeps a compact label to one line at a capped size', async () => {
        atFontScale(3);
        await render(
            <>
                <Text numberOfLines={1} testID="title">A long row title</Text>
                <Text numberOfLines={1} compact testID="chip">Chip</Text>
            </>,
            { wrapper: wrap },
        );
        expect(screen.getByTestId('title').props.numberOfLines).toBeUndefined();
        expect(screen.getByTestId('chip').props.numberOfLines).toBe(1);
        expect(screen.getByTestId('chip').props.maxFontSizeMultiplier).toBe(1.3);
    });

    it('keeps the one-line text at the default size', async () => {
        atFontScale(1);
        await render(<Text numberOfLines={1} testID="title">A long row title</Text>, { wrapper: wrap });
        expect(screen.getByTestId('title').props.numberOfLines).toBe(1);
    });

    it('wraps a label beside its value, and lets both shrink', async () => {
        atFontScale(3);
        await render(
            <Row justify="between" testID="row">
                <Text>Label</Text>
                <Text>Value</Text>
            </Row>,
            { wrapper: wrap },
        );
        expect(screen.getByTestId('row').props.style).toMatchObject({ flexWrap: 'wrap' });
        expect(screen.getByText('Label').parent?.props.style).toMatchObject({ flexShrink: 1 });
    });
});
