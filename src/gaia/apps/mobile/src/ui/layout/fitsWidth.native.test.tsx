// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform, StyleSheet } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { translate } from '@webui/i18n/registry';

import { Button, Row, Text, ThemeProvider } from '@/ui';

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const wrap = ({ children }: { children: ReactNode }) => (
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
        <ThemeProvider>{children}</ThemeProvider>
    </SafeAreaProvider>
);

// "رقم 1 في الدور (#1 in line)": the Arabic UI carries the English in brackets.
const glossed = translate('ar', 'row.inLine', { n: 1 });

describeNative('Arabic text keeps its room', () => {
    it('lets a button shrink inside its row and wrap its label instead of running off the screen', async () => {
        await render(
            <Row gap="sm">
                <Button label={translate('ar', 'row.cancelRename')} />
                <Button label={translate('ar', 'row.saveName')} testID="save" />
            </Row>,
            { wrapper: wrap },
        );
        const outer = StyleSheet.flatten(screen.getByTestId('save').parent?.props.style);
        expect(outer).toMatchObject({ flexShrink: 1, maxWidth: '100%' });
        expect(screen.getByText(translate('ar', 'row.saveName')).props.numberOfLines).toBeUndefined();
    });

    it('keeps a wrapping row inside its parent row', async () => {
        await render(
            <Row testID="outer" wrap>
                <Row testID="inner" wrap>
                    <Text>{glossed}</Text>
                </Row>
            </Row>,
            { wrapper: wrap },
        );
        expect(StyleSheet.flatten(screen.getByTestId('inner').props.style).maxWidth).toBe('100%');
    });
});
