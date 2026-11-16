// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { createHttpClient } from '@/api/http';
import { isDeviceRefused } from '@/api/routeAccess';
import { GaiaClientProvider } from '@/data';
import { useConnectionStore } from '@/state/connection';
import { createRecordingFetch, jsonResponse } from '@/test-support/fakeFetch';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import Model from './Model';

jest.mock('./model/ModelList', () => ({ ModelList: () => null }));
jest.mock('./model/CloudSpending', () => ({ CloudSpending: () => null }));
jest.mock('./model/CloudPrices', () => ({ CloudPrices: () => null }));

// The web project's renderer can't mount react-native-web's DOM or the sheet host.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const BASE = 'https://pc.local:4443';
const ACTIVE = { provider: 'fireworks', model: 'accounts/fireworks/models/x', display_name: 'X', label: null, remote: true, is_default: false, downloaded: true };

function pathOf(url: string): string {
    return new URL(url).pathname;
}

describeNative('Settings > Model on a paired phone', () => {
    afterEach(() => useConnectionStore.setState({ connection: null }));

    it('asks the PC for no route it refuses this phone, and says to change it on the PC instead of failing', async () => {
        useConnectionStore.setState({ connection: { baseUrl: BASE, pcId: 'pc-1', pinned: true } });
        const { fetch, calls } = createRecordingFetch((call) => {
            const path = pathOf(call.url);
            if (isDeviceRefused(call.method ?? 'GET', path)) return jsonResponse({ detail: 'Do this on the PC running GAIA.' }, 403);
            if (path === '/api/providers/active') return jsonResponse(ACTIVE);
            if (path === '/api/providers/list') {
                return jsonResponse({
                    providers: [{ id: 'fireworks', name: 'Fireworks AI', remote: true, privacy_notice: '', configured: true, blocked: false }],
                    lemonade_error: null,
                });
            }
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
        await render(<Model />, { wrapper: wrap });

        expect((await screen.findAllByText('Change this on the PC running GAIA.')).length).toBeGreaterThan(0);
        expect(calls.some((c) => pathOf(c.url) === '/api/providers/active')).toBe(true);
        expect(calls.filter((c) => isDeviceRefused(c.method ?? 'GET', pathOf(c.url))).map((c) => `${c.method} ${c.url}`)).toEqual([]);
        expect(screen.queryByText('Do this on the PC running GAIA.')).toBeNull();
        queryClient.clear();
    });
});
