// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { createHttpClient } from '@/api/http';
import { GaiaClientProvider } from '@/data';
import { useConnectionStore } from '@/state/connection';
import { createRecordingFetch, jsonResponse } from '@/test-support/fakeFetch';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import Privacy from './Privacy';

// The web project's renderer can't mount react-native-web's DOM or the sheet host.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const BASE = 'https://pc.local:4443';

function pathOf(url: string): string {
    return new URL(url).pathname;
}

describeNative('Settings > Privacy on a paired phone', () => {
    afterEach(() => useConnectionStore.setState({ connection: null }));

    it('shows Local only read-only with the PC-only note, and sends no settings change', async () => {
        useConnectionStore.setState({ connection: { baseUrl: BASE, pcId: 'pc-1', pinned: true } });
        const { fetch, calls } = createRecordingFetch((call) => {
            if (pathOf(call.url) === '/api/settings') return jsonResponse({ network_policy: 'cloud_allowed', policy_notices: [] });
            return jsonResponse({ detail: 'Not Found' }, 404);
        });
        const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const wrap = ({ children }: { children: ReactNode }) => (
            <QueryClientProvider client={queryClient}>
                <GaiaClientProvider client={createHttpClient({ baseUrl: BASE, fetch })}>
                    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
                        <ThemeProvider>
                            <SheetHost>{children}</SheetHost>
                        </ThemeProvider>
                    </SafeAreaProvider>
                </GaiaClientProvider>
            </QueryClientProvider>
        );
        await render(<Privacy />, { wrapper: wrap });

        expect(await screen.findByTestId('privacy-pc-only')).toBeTruthy();
        expect(screen.queryByTestId('privacy-local-only')).toBeNull();
        expect(calls.filter((c) => c.method === 'PUT' && pathOf(c.url) === '/api/settings')).toEqual([]);
        queryClient.clear();
    });
});
