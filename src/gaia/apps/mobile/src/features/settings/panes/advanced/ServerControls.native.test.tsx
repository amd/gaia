// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { GaiaApiError } from '@/api/errors';
import type { LemonadeTarget } from '@/api/client.types';
import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import { ServerControlsSlot } from './ServerControls';

// Some Expo modules read Expo's config, which needs APP_ENV; jest doesn't set it.
jest.mock('expo-constants', () => ({ __esModule: true, default: { expoConfig: { version: 'test' } } }));

// The web project's renderer can't mount react-native-web's DOM or the sheet host.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const SERVER = {
    reachable: true,
    owner: 'user',
    version: '10.8',
    base_url: 'http://localhost:13305',
    chat_model: null,
    embedding_model: null,
    loaded_models: [],
    available_backends: [{ name: 'vulkan', state: 'installed' }, { name: 'rocm', state: 'installed' }],
    preferred_backend: 'vulkan',
    default_backend: 'vulkan',
} as never;

const TARGET = { mode: 'external', source: 'config', base_url: 'http://localhost:13305', saved_base_url: 'http://localhost:13305', api_key_stored: false, gaia_installed: true, offer: false } as unknown as LemonadeTarget;

describeNative('Model server controls', () => {
    it('a 403 on the backend or on Use GAIA’s own shows the PC-only notice and leaves the controls as they were', async () => {
        const client = createFakeClient({
            modelServer: SERVER,
            lemonadeTarget: TARGET,
            serverChangeError: new GaiaApiError(403, 'Do this on the PC running GAIA.'),
        });
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
        await render(<ServerControlsSlot />, { wrapper: wrap });
        fireEvent.press(await screen.findByText('Model server settings'));

        fireEvent.press(await screen.findByText('ROCm'));
        await screen.findByTestId('advanced-pc-only');
        expect(screen.getByRole('radio', { name: 'Vulkan', checked: true })).toBeTruthy();

        fireEvent.press(await screen.findByTestId('advanced-use-own'));
        await screen.findAllByTestId('advanced-pc-only');
        expect(screen.getByText(/GAIA uses your Lemonade Server at .*localhost:13305/)).toBeTruthy();
        expect(client.backendSets).toEqual([]);
        expect(client.targetSets).toEqual([]);
        queryClient.clear();
    });
});
