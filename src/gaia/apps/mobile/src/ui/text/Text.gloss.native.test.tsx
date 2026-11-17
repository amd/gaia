// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
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
        expect(block.props.style).toMatchObject({ writingDirection: 'ltr', textAlign: 'left', marginTop: 4 });
        expect(screen.getByTestId('t')).not.toHaveTextContent('(');
    });

    it('keeps a short gloss inline with brackets', async () => {
        await render(<Text testID="t">{glossed('إضافة مهارات', SHORT)}</Text>, { wrapper: wrap });
        expect(screen.queryByTestId('t-gloss')).toBeNull();
        expect(screen.getByTestId('t')).toHaveTextContent(`(${SHORT})`, { exact: false });
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
