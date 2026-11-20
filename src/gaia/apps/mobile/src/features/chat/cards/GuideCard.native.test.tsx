// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react-native';
import { Platform } from 'react-native';

import { translator } from '@/i18n/useT';
import { ThemeProvider } from '@/ui';

import { RenderCard } from './RenderCard';

jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(() => Promise.resolve(true)) }));
jest.mock('expo-constants', () => ({ __esModule: true, default: { expoConfig: { version: 'test' } } }));
jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));

const t = translator('en');
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const show = (render_: string, data: unknown) =>
    render(
        <ThemeProvider>
            <RenderCard render={render_} data={data} />
        </ThemeProvider>,
    );

const RECIPE = {
    kind: 'guide',
    guideType: 'recipe',
    title: 'Pancakes',
    servings: 4,
    items: ['2 cups flour', '1 cup milk', '2 large eggs'],
    steps: ['Whisk everything, then rest the batter for 10 minutes.'],
};

describeNative('guide card', () => {
    it('rescales amounts with the servings stepper and converts them with the unit switch', async () => {
        await show('guide', RECIPE);
        expect(screen.getByText(/2 cups/)).toBeTruthy();
        expect(screen.getByText(/2 large eggs/)).toBeTruthy();

        await fireEvent.press(screen.getByLabelText(t('cards.moreServings')));
        await fireEvent.press(screen.getByLabelText(t('cards.moreServings')));
        await fireEvent.press(screen.getByLabelText(t('cards.moreServings')));
        await fireEvent.press(screen.getByLabelText(t('cards.moreServings')));
        expect(screen.getByText(/4 cups/)).toBeTruthy();
        expect(screen.getByText(/4 large eggs/)).toBeTruthy();

        await fireEvent.press(screen.getByLabelText(t.bare('cards.system.metric')));
        expect(screen.queryByText(/4 cups/)).toBeNull();
        expect(screen.getAllByText(/\d\s?(ml|l)\b/i)).toHaveLength(2);
    });
});

describeNative('calculator card', () => {
    it('computes a loan payment as the web does', async () => {
        await show('calculator', { kind: 'calculator', calculator: 'loan', currency: 'USD', inputs: { amount: 300000, annualRatePercent: 6, years: 30, downPayment: 0 } });
        expect(screen.getByText(/\$1,798\.65/)).toBeTruthy();
        expect(screen.getByText(/\$647,514\./)).toBeTruthy();
    });

    it('ignores out-of-range typing and keeps the last good result', async () => {
        await show('calculator', { kind: 'calculator', calculator: 'tip', currency: 'USD', inputs: { bill: 100, tipPercent: 20, people: 2 } });
        expect(screen.getByText(/\$60\.00/)).toBeTruthy();
        await fireEvent.changeText(screen.getByTestId('calc-input-tipPercent'), '150');
        expect(screen.getByText(/\$60\.00/)).toBeTruthy();
        await fireEvent.changeText(screen.getByTestId('calc-input-tipPercent'), '10');
        expect(screen.getByText(/\$55\.00/)).toBeTruthy();
    });
});
