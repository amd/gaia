// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform, StyleSheet, processColor } from 'react-native';

import { colors } from '../tokens/generated';
import { withDerivedColors } from '../theme/appearance';
import { useAppearance } from '../theme/appearanceStore';
import { ThemeProvider } from '../theme/ThemeProvider';
import { Button } from './Button';
import { IconButton } from './IconButton';
import { SmallButton } from './SmallButton';

const wrap = ({ children }: { children: ReactNode }) => <ThemeProvider>{children}</ThemeProvider>;
const Glyph = () => null;
const android = Platform.OS === 'android';
const describeNative = Platform.OS === 'web' ? describe.skip : describe;
const ripple = () => withDerivedColors(colors.light).ripple;

describeNative('press feedback', () => {
    afterEach(() => useAppearance.getState().reset());

    it('ripples an icon-only control as a circle of half its 48 dp target', async () => {
        useAppearance.getState().setScheme('light');
        const onPress = jest.fn();
        await render(<IconButton icon={Glyph as never} label="Close" onPress={onPress} variant="raised" testID="close" />, { wrapper: wrap });
        const target = screen.getByTestId('close');
        if (android) {
            expect(target.props.nativeForegroundAndroid).toMatchObject({ type: 'RippleAndroid', borderless: true, rippleRadius: 24, color: processColor(ripple()) });
        } else {
            expect(target.props.nativeForegroundAndroid).toBeUndefined();
        }
        expect(StyleSheet.flatten(target.props.style)).toMatchObject({ minWidth: 48, minHeight: 48 });
        await fireEvent.press(target);
        expect(onPress).toHaveBeenCalledTimes(1);
    });

    it('clips a small button ripple to its drawn shape and keeps the 44 pt target', async () => {
        useAppearance.getState().setScheme('light');
        const onPress = jest.fn();
        await render(<SmallButton label="Edit" onPress={onPress} testID="edit" />, { wrapper: wrap });
        await fireEvent.press(screen.getByTestId('edit'));
        expect(onPress).toHaveBeenCalledTimes(1);
        if (!android) {
            expect(screen.queryByTestId('edit-clip')).toBeNull();
            expect(StyleSheet.flatten(screen.getByTestId('edit').props.style)).toMatchObject({ minHeight: 44 });
            return;
        }
        expect(screen.getByTestId('edit').props.nativeForegroundAndroid).toMatchObject({ borderless: false, color: processColor(ripple()) });
        expect(StyleSheet.flatten(screen.getByTestId('edit-clip').props.style)).toMatchObject({ overflow: 'hidden', borderRadius: expect.any(Number) });
        expect(StyleSheet.flatten(screen.getByTestId('edit-target').props.style)).toMatchObject({ minHeight: 44, alignItems: 'flex-start' });
        // Hit slop grows the 30 pt shape back to the 44 pt target once it is measured.
        await fireEvent(screen.getByTestId('edit-clip'), 'layout', { nativeEvent: { layout: { x: 0, y: 0, width: 60, height: 30 } } });
        expect(screen.getByTestId('edit-clip').props.hitSlop).toEqual({ left: 0, right: 0, top: 7, bottom: 7 });
        expect(screen.getByTestId('edit').props.hitSlop).toEqual({ left: 0, right: 0, top: 7, bottom: 7 });
    });

    it('clips a small Button the same way, filling its width', async () => {
        await render(<Button size="sm" label="Retry" testID="retry" />, { wrapper: wrap });
        if (!android) {
            expect(screen.queryByTestId('retry-clip')).toBeNull();
            return;
        }
        expect(StyleSheet.flatten(screen.getByTestId('retry-target').props.style)).toMatchObject({ alignItems: 'stretch' });
        expect(StyleSheet.flatten(screen.getByTestId('retry-clip').props.style)).toMatchObject({ overflow: 'hidden' });
    });

    it('takes the ripple color from the theme in dark and light', async () => {
        useAppearance.getState().setScheme('dark');
        await render(<Button label="Start" testID="start" />, { wrapper: wrap });
        if (android) {
            expect(screen.getByTestId('start').props.nativeForegroundAndroid).toMatchObject({ color: processColor(withDerivedColors(colors.dark).ripple) });
        }
    });
});
