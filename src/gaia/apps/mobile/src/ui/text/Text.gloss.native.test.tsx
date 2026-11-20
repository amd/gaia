// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform, StyleSheet } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { isolate } from '@webui/i18n/registry';

import { useLocaleStore } from '@/state/locale';
import { Button, Text, ThemeProvider } from '@/ui';
import { isLongGloss } from './Text';

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const wrap = ({ children }: { children: ReactNode }) => (
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
        <ThemeProvider>{children}</ThemeProvider>
    </SafeAreaProvider>
);

const glossed = (arabic: string, english: string) => `${isolate(arabic, 'rtl')} ${isolate(`(${english})`, 'ltr')}`;
const LONG = 'Skills are saved instructions. Add one to teach GAIA a task.';
const SHORT = 'Add skills';

beforeEach(() => useLocaleStore.getState().setLocale('ar'));
afterEach(() => useLocaleStore.getState().setLocale(null));

describe('isLongGloss', () => {
    it('is long with two sentences or over 40 characters', () => {
        expect(isLongGloss('Do this. Then that')).toBe(true);
        expect(isLongGloss('x'.repeat(41))).toBe(true);
        expect(isLongGloss('Add skills.')).toBe(false);
        expect(isLongGloss('Change this on the PC running GAIA.')).toBe(true);
        expect(isLongGloss('Close GAIA to finish switching to Arabic')).toBe(false);
        expect(isLongGloss('Close GAIA to finish switching to Arabic', 29)).toBe(true);
        expect(isLongGloss(SHORT)).toBe(false);
    });
});

describeNative('Text with a long English gloss', () => {
    it('renders the English as its own block, left to right, without brackets', async () => {
        await render(<Text testID="t">{glossed('إضافة مهارات', LONG)}</Text>, { wrapper: wrap });
        const block = screen.getByTestId('t-gloss');
        expect(block.props.children).toBe(LONG);
        expect(block.props.style).toMatchObject({ writingDirection: 'ltr', textAlign: 'right', marginTop: 4 });
        expect(block.props.style.textAlign).toBe(StyleSheet.flatten(screen.getByTestId('t').props.style).textAlign);
        expect(screen.getByTestId('t')).not.toHaveTextContent('(');
    });

    it('puts the block on the start edge in English too', async () => {
        useLocaleStore.getState().setLocale(null);
        await render(<Text testID="t">{glossed('Add skills', LONG)}</Text>, { wrapper: wrap });
        const main = StyleSheet.flatten(screen.getByTestId('t').props.style).textAlign;
        expect(screen.getByTestId('t-gloss').props.style.textAlign).toBe(main);
        expect(main).toBe('left');
    });

    it('pins an inline gloss line to the UI direction', async () => {
        await render(<Text testID="t">{glossed('عام', 'General')}</Text>, { wrapper: wrap });
        expect(StyleSheet.flatten(screen.getByTestId('t').props.style).writingDirection).toBe('rtl');
        useLocaleStore.getState().setLocale(null);
        await render(<Text testID="e">{glossed('General', 'General')}</Text>, { wrapper: wrap });
        expect(StyleSheet.flatten(screen.getByTestId('e').props.style).writingDirection).toBe('ltr');
    });

    it('keeps a short gloss inline with brackets', async () => {
        await render(<Text testID="t">{glossed('إضافة مهارات', SHORT)}</Text>, { wrapper: wrap });
        expect(screen.queryByTestId('t-gloss')).toBeNull();
        expect(screen.getByTestId('t')).toHaveTextContent(`(${SHORT})`, { exact: false });
    });

    it('keeps an inline gloss together and leaves the line direction to the paragraph', async () => {
        await render(<Text testID="t">{glossed('إضافة مهارات', 'Show resource usage')}</Text>, { wrapper: wrap });
        const outer = screen.getByTestId('t');
        const span = outer.children.find((c) => typeof c !== 'string') as unknown as { props: { style: Record<string, unknown>; children: string } };
        expect(span.props.style.writingDirection).toBeUndefined();
        expect(outer.props.style).not.toMatchObject({ writingDirection: 'ltr' });
        expect(span.props.children).not.toContain(' ');
        expect(span.props.children).toContain('Show\u00A0resource\u00A0usage');
    });

    it('wraps a block gloss normally', async () => {
        await render(<Text testID="t">{glossed('إضافة مهارات', LONG)}</Text>, { wrapper: wrap });
        expect(screen.getByTestId('t-gloss').props.children).toContain(' ');
    });

    it('stays inline in a nested span', async () => {
        await render(
            <Text testID="outer">
                <Text testID="inner">{glossed('إضافة مهارات', LONG)}</Text>
            </Text>,
            { wrapper: wrap },
        );
        expect(screen.queryByTestId('inner-gloss')).toBeNull();
        expect(screen.getByTestId('inner')).toHaveTextContent(`(${LONG})`, { exact: false });
    });

    it('stays inline in a Button label', async () => {
        await render(<Button testID="b" label={glossed('إضافة مهارات', LONG)} onPress={() => {}} />, { wrapper: wrap });
        expect(screen.queryByTestId('b-gloss')).toBeNull();
        expect(screen.getByText(new RegExp(`\\(${LONG.slice(0, 20)}`))).toBeTruthy();
    });

    it('stays inline when numberOfLines is set', async () => {
        await render(
            <Text testID="t" numberOfLines={2}>
                {glossed('إضافة مهارات', LONG)}
            </Text>,
            { wrapper: wrap },
        );
        expect(screen.queryByTestId('t-gloss')).toBeNull();
    });
});
