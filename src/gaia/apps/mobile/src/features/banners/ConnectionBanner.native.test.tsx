// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import type { SystemStatus } from '@/api/client.types';
import { STATUS_IDLE_POLL_MS } from '@/api/constants';
import { GaiaNetworkError } from '@/api/errors';
import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { shouldRetryQuery } from '@/data/queryRetry';
import { translator } from '@/i18n/useT';
import { ThemeProvider } from '@/ui';

import { ConnectionBanner } from './ConnectionBanner';

const describeNative = Platform.OS === 'web' ? describe.skip : describe;
const t = translator('en');

const healthy = {
    lemonade_running: true,
    context_size_sufficient: true,
    model_context_size: 65536,
    model_loaded: 'Gemma-4-E4B-it-GGUF',
    init_state: 'ready',
    download_progress: null,
} as unknown as SystemStatus;

async function advance(ms: number) {
    for (let spent = 0; spent < ms; spent += 1_000) await act(async () => void (await jest.advanceTimersByTimeAsync(1_000)));
}

describeNative('ConnectionBanner', () => {
    beforeEach(() => jest.useFakeTimers());
    afterEach(() => jest.useRealTimers());

    it("says the PC can't be reached once a PC that answered stops, and clears when it answers again", async () => {
        let up = true;
        let polls = 0;
        const client = createFakeClient({ status: healthy });
        client.getSystemStatus = () => {
            polls += 1;
            return up ? Promise.resolve(healthy) : Promise.reject(new GaiaNetworkError('https://pc', new Error('Connection refused')));
        };
        const queryClient = new QueryClient({ defaultOptions: { queries: { retry: shouldRetryQuery } } });
        const wrapper = ({ children }: { children: ReactNode }) => (
            <QueryClientProvider client={queryClient}>
                <GaiaClientProvider client={client}>
                    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
                        <ThemeProvider>{children}</ThemeProvider>
                    </SafeAreaProvider>
                </GaiaClientProvider>
            </QueryClientProvider>
        );
        await render(<ConnectionBanner />, { wrapper });
        await advance(1_000);
        expect(screen.queryByTestId('banner-unreachable')).toBeNull();

        up = false;
        // The next idle poll is the first one refused; its two retries take 1 s and 2 s.
        await advance(STATUS_IDLE_POLL_MS + 5_000);
        expect(screen.getByTestId('banner-unreachable')).toBeTruthy();
        expect(screen.getByText(t('mobileApp.pairing.off.title'))).toBeTruthy();

        up = true;
        await advance(10_000);
        expect(screen.queryByTestId('banner-unreachable')).toBeNull();

        // Back to the healthy rate once it answers, not the outage's fast one.
        const before = polls;
        await advance(2 * STATUS_IDLE_POLL_MS);
        expect(polls - before).toBe(2);
    });
});
