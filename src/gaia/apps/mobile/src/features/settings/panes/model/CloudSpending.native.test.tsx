// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';

import { CloudSpending } from './CloudSpending';

// The web project's renderer can't mount react-native-web's DOM.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

describeNative('CloudSpending', () => {
    it('saving the limit sends the PUT, and bad input is refused with a message', async () => {
        const client = createFakeClient({
            spending: {
                month: '2026-11', total_usd: 1.5, turns: 3, unpriced_turns: 0, unreported_turns: 0,
                limit_usd: null, state: 'no_limit', by_model: [], price_source: '',
            },
        });
        const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const wrap = ({ children }: { children: ReactNode }) => (
            <QueryClientProvider client={queryClient}>
                <GaiaClientProvider client={client}>
                    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
                        <ThemeProvider>{children}</ThemeProvider>
                    </SafeAreaProvider>
                </GaiaClientProvider>
            </QueryClientProvider>
        );
        await render(<CloudSpending />, { wrapper: wrap });
        const input = await screen.findByTestId('spend-limit-input');

        await fireEvent.changeText(input, '-5');
        await screen.findByText('Enter an amount above zero, like 20.');
        expect(screen.getByTestId('spend-limit-save').props.accessibilityState).toMatchObject({ disabled: true });

        await fireEvent.changeText(input, '20');
        await waitFor(() => expect(screen.getByTestId('spend-limit-save').props.accessibilityState).toMatchObject({ disabled: false }));
        await fireEvent.press(screen.getByTestId('spend-limit-save'));
        await waitFor(() => expect(client.spendingLimitSets).toEqual([20]));
        await screen.findByText(/of your .*\$20\.00.* limit/);
        queryClient.clear();
    });
});
