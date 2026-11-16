// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, type ReactElement } from 'react';
import { BackHandler, Text as RNText } from 'react-native';
import { createRoot } from 'test-renderer';
import { Sheet } from './Sheet';
import { SheetHost } from './SheetHost';
import { sheetStore } from './sheetStore';

jest.mock('react-native-reanimated', () => {
    const { View } = jest.requireActual('react-native');
    return {
        __esModule: true,
        default: { View },
        Easing: { bezier: () => undefined },
        useAnimatedStyle: () => ({}),
        useReducedMotion: () => true,
        useSharedValue: (value: number) => ({ value }),
        withTiming: (value: number) => value,
    };
});
jest.mock('react-native-gesture-handler', () => ({
    GestureDetector: ({ children }: { children: ReactElement }) => children,
    usePanGesture: () => ({}),
}));
jest.mock('react-native-keyboard-controller', () => ({
    KeyboardAvoidingView: ({ children }: { children: ReactElement }) => children,
}));
jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, start: 0, end: 0 }) }));
jest.mock('@/i18n/useT', () => ({ useT: () => (key: string) => key, useDirection: () => 'ltr' }));
jest.mock('../theme/ThemeProvider', () => {
    const { colors, radius, shadows } = jest.requireActual('../tokens/generated');
    const { space } = jest.requireActual('../tokens/space');
    const theme = { scheme: 'light', colors: colors.light, radius, space, shadows: shadows.light };
    return { useTheme: () => theme };
});
jest.mock('../controls/IconButton', () => ({ IconButton: () => null }));
jest.mock('../icons', () => ({ X: () => null }));
jest.mock('../text/Text', () => ({ Text: ({ children }: { children: unknown }) => children }));

function Screen({ a, b, onCloseA, onCloseB }: { a: boolean; b: boolean; onCloseA: () => void; onCloseB: () => void }) {
    return (
        <SheetHost>
            <Sheet open={a} onClose={onCloseA}>
                <RNText>first</RNText>
            </Sheet>
            <Sheet open={b} onClose={onCloseB}>
                <RNText>second</RNText>
            </Sheet>
        </SheetHost>
    );
}

const shown = (r: ReturnType<typeof createRoot>) => JSON.stringify(r.container.toJSON());

describe('SheetHost', () => {
    beforeEach(() => {
        jest.useFakeTimers();
        sheetStore.reset();
    });
    afterEach(() => jest.useRealTimers());

    it('shows one sheet at a time, and Android back closes the top one', async () => {
        const closeA = jest.fn();
        const closeB = jest.fn();
        let backHandlers: (() => boolean)[] = [];
        jest.spyOn(BackHandler, 'addEventListener').mockImplementation((_e, h) => {
            backHandlers.push(h as () => boolean);
            return { remove: () => (backHandlers = backHandlers.filter((x) => x !== (h as () => boolean))) };
        });

        const r = createRoot({ textComponentTypes: ['Text', 'RCTText'] });
        await act(async () => {
            r.render(<Screen a b onCloseA={closeA} onCloseB={closeB} />);
        });
        expect(shown(r)).toContain('first');
        expect(shown(r)).not.toContain('second');

        await act(async () => {
            expect(backHandlers[backHandlers.length - 1]()).toBe(true);
        });
        expect(closeA).toHaveBeenCalledTimes(1);
        expect(closeB).not.toHaveBeenCalled();

        // The parent closes the first; the second waits until it has fully gone.
        await act(async () => {
            r.render(<Screen a={false} b onCloseA={closeA} onCloseB={closeB} />);
        });
        expect(shown(r)).toContain('first');
        expect(shown(r)).not.toContain('second');
        await act(async () => {
            jest.runAllTimers();
        });
        expect(shown(r)).toContain('second');
        expect(shown(r)).not.toContain('first');
        // The first render transforms React Native's lazy components on a cold cache (about 1.6 s measured, 25 ms warm), which a loaded machine stretches past 5 s.
    }, 20_000);
});
