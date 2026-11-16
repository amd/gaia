// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import type { SetupCatalogEntry, SetupPlan } from '@webui/types';

import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import { LocalModels } from './LocalModels';

jest.mock('expo-constants', () => ({ __esModule: true, default: { expoConfig: { version: 'test' } } }));

// The web project's renderer can't mount react-native-web's DOM or the sheet host.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const entry = (e: Partial<SetupCatalogEntry>): SetupCatalogEntry => ({
    id: 'm', name: 'M', roles: ['chat'], purpose: '', if_skipped: null, recipe: null, size_gb: 10, full_size_gb: 10,
    partial: false, partial_gb: 0, downloaded: true, in_lemonade: true, recommended: false, planned: true,
    gaia_model: true, selected: true, skipped: false, chat_choice: true, support: 'verified', reads_images: false,
    feature_tier: 'full', source: null, fits: true, need_gb: 12, capacity_gb: 64, fit_reason: null, ...e,
});

const plan: SetupPlan = {
    lemonade: { mode: 'gaia', url: null, reachable: true },
    chat_model: { id: 'chat', user_set: false, size_gb: 10, downloaded: true },
    recommended_model: 'chat', decided_after_server: false, memory_gb: 64, memory_source: null, options: [], models: [],
    download_gb: 0, disk: { path: '/', free_gb: 100, source: 'local' },
    catalog: [entry({ id: 'chat', name: 'Chat-GGUF' }), entry({ id: 'embed', name: 'Embed-GGUF', roles: ['embedding'], chat_choice: false, size_gb: 2, full_size_gb: 2 })],
};

describeNative('Local models', () => {
    it('Remove asks first, Keep deletes nothing, and Remove then calls the PC once', async () => {
        const client = createFakeClient({ setupPlan: plan, setupStatus: { state: 'idle' } });
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
        await render(<LocalModels />, { wrapper: wrap });

        await fireEvent.press(screen.getByTestId('local-models-toggle'));
        expect(screen.queryByTestId('local-remove-chat')).toBeNull();
        await fireEvent.press(await screen.findByTestId('local-remove-embed'));
        await screen.findByText('Remove Embed-GGUF?');
        expect(client.removals).toEqual([]);

        await fireEvent.press(screen.getByTestId('confirm-cancel'));
        expect(client.removals).toEqual([]);

        // The cancelled sheet is still sliding out, so the row reads as hidden for a moment.
        await fireEvent.press(await screen.findByTestId('local-remove-embed', { includeHiddenElements: true }));
        await fireEvent.press(await screen.findByTestId('confirm-ok'));
        await waitFor(() => expect(client.removals).toEqual(['embed']));
        queryClient.clear();
    });
});
