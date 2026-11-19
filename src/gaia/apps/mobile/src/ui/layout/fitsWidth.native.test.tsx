// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform, StyleSheet } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { translate } from '@webui/i18n/registry';

import { Button, ListRow, Notice, Row, Text, ThemeProvider } from '@/ui';

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
        expect(outer).toMatchObject({ flexShrink: 1, maxWidth: '100%', minWidth: 88 });
        expect(StyleSheet.flatten(screen.getByTestId('save').props.style).minHeight).toBeGreaterThanOrEqual(44);
        const label = screen.getByText(translate('ar', 'row.saveName'));
        expect(label.props.numberOfLines).toBeUndefined();
        expect(StyleSheet.flatten(label.props.style).textAlign).toBe('center');
    });

    it('keeps a notice action at its own width so the text wraps first', async () => {
        await render(<Notice tone="info" message="A long message that should wrap" action={{ label: 'Retry', onPress: () => {} }} />, { wrapper: wrap });
        const action = screen.getByText('Retry');
        let node = action.parent;
        while (node && !StyleSheet.flatten(node.props.style)?.alignSelf) node = node.parent;
        expect(StyleSheet.flatten(node?.props.style)).toMatchObject({ flexShrink: 0 });
        let text = screen.getByText('A long message that should wrap').parent;
        while (text && StyleSheet.flatten(text.props.style)?.flex !== 1) text = text.parent;
        expect(text).toBeTruthy();
    });

    it('keeps a trailing list-row button at its own width', async () => {
        await render(<ListRow title="A long row title" trailing={<Button label="Use" testID="use" shrink={false} />} />, { wrapper: wrap });
        expect(StyleSheet.flatten(screen.getByTestId('use').parent?.props.style)).toMatchObject({ flexShrink: 0 });
        expect(StyleSheet.flatten(screen.getByTestId('use').parent?.parent?.props.style)).toMatchObject({ flexShrink: 0 });
        expect(StyleSheet.flatten(screen.getByText('A long row title').parent?.props.style)).toMatchObject({ flex: 1 });
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
