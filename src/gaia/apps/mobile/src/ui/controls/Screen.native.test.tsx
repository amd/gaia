// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform, StyleSheet, Text } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { Screen, ThemeProvider } from '@/ui';
import { space } from '../tokens/space';

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const wrap = ({ children }: { children: ReactNode }) => (
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
        <ThemeProvider>{children}</ThemeProvider>
    </SafeAreaProvider>
);

describeNative('Screen footer', () => {
    it('keeps the footer a gutter away from the screen edges', async () => {
        await render(<Screen footer={<Text>Actions</Text>} />, { wrapper: wrap });
        const style = StyleSheet.flatten(screen.getByTestId('screen-footer').props.style);
        expect(style.paddingHorizontal).toBe(space.gutter);
    });

    it('lets a bleed footer span the full width', async () => {
        await render(<Screen footerBleed footer={<Text>Actions</Text>} />, { wrapper: wrap });
        const style = StyleSheet.flatten(screen.getByTestId('screen-footer').props.style);
        expect(style?.paddingHorizontal).toBeUndefined();
    });

    it('draws a hairline above a footer with a divider', async () => {
        await render(<Screen footerDivider footer={<Text>Actions</Text>} />, { wrapper: wrap });
        const style = StyleSheet.flatten(screen.getByTestId('screen-footer').props.style);
        expect(style.borderTopWidth).toBe(StyleSheet.hairlineWidth);
        expect(style.paddingVertical).toBe(space.md);
    });
});
