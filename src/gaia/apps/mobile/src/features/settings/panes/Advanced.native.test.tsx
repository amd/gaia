// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { GaiaApiError } from '@/api/errors';
import type { SystemStatus } from '@/api/client.types';
import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import Advanced from './Advanced';

// Some Expo modules read Expo's config, which needs APP_ENV; jest doesn't set it.
jest.mock('expo-constants', () => ({ __esModule: true, default: { expoConfig: { version: 'test' } } }));

// The Logs card has its own tests, and reading Expo's config needs APP_ENV, which jest doesn't set.
jest.mock('@/features/logs', () => ({ LogsCard: () => null }));

// The web project's renderer can't mount react-native-web's DOM or the sheet host.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const WRONG_MODEL = {
    lemonade_running: true,
    lemonade_version: '10.8',
    model_loaded: 'Other-Model',
    expected_model_loaded: false,
    context_size_sufficient: true,
    model_downloaded: true,
    default_model_name: 'Gemma-4-E4B-it-GGUF',
    disk_space_gb: 120,
    memory_available_gb: 40,
    embedding_model_loaded: true,
    download_progress: null,
} as unknown as SystemStatus;

describeNative('Advanced pane', () => {
    it('a 403 on Load model shows the PC-only notice and leaves the model row as it was', async () => {
        const client = createFakeClient({ status: WRONG_MODEL, modelActionError: new GaiaApiError(403, 'Do this on the PC running GAIA.') });
        const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const wrap = ({ children }: { children: ReactNode }) => (
            <QueryClientProvider client={queryClient}>
                <GaiaClientProvider client={client}>
                    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
                        <ThemeProvider>
                            <SheetHost>{children}</SheetHost>
                        </ThemeProvider>
                    </SafeAreaProvider>
                </GaiaClientProvider>
            </QueryClientProvider>
        );
        await render(<Advanced />, { wrapper: wrap });
        await screen.findByText('Wrong model loaded.');

        fireEvent.press(screen.getByTestId('advanced-load'));

        await screen.findByText('Change this on the PC running GAIA.');
        expect(screen.getByText('Wrong model loaded.')).toBeTruthy();
        expect(screen.getByText(/Other-Model/)).toBeTruthy();
        expect(screen.getByText('Load model')).toBeTruthy();
        expect(client.modelLoads).toEqual([]);
        queryClient.clear();
    });
});
