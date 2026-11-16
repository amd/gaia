// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import type { ProviderModel } from '@/api/client.types';
import { GaiaApiError } from '@/api/errors';
import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import { ModelList } from './ModelList';

// The web project's renderer can't mount react-native-web's DOM or the sheet host.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const model = (id: string, extra: Partial<ProviderModel>): ProviderModel => ({
    id,
    display_name: id,
    context_length: null,
    downloaded: false,
    labels: [],
    rank: null,
    note: null,
    evidence: null,
    available: true,
    ...extra,
});

describeNative('Model list', () => {
    it('groups by the server flags, and a 403 on Download shows the PC-only notice and leaves the row as it was', async () => {
        const client = createFakeClient({
            modelDownloadError: new GaiaApiError(403, 'Do this on the PC running GAIA.'),
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
        const models = [
            model('Have-GGUF', { downloaded: true }),
            model('Want-GGUF', {}),
            model('Never-GGUF', { available: false, unavailable_reason: 'needs 64 GB' }),
        ];
        await render(
            <ModelList provider="local" remote={false} models={models} activeModel="Have-GGUF" switching={null} onUse={jest.fn()} onRefresh={jest.fn()} />,
            { wrapper: wrap },
        );

        expect(screen.getByTestId('model-group-downloaded')).toHaveTextContent(/Have-GGUF/);
        expect(screen.getByTestId('model-group-available')).toBeTruthy();
        expect(screen.queryByTestId('model-download-Want-GGUF')).toBeNull();
        await fireEvent.press(screen.getByLabelText('Available to download'));
        expect(screen.getByTestId('model-group-unavailable')).toBeTruthy();
        expect(screen.queryByTestId('model-download-Never-GGUF')).toBeNull();

        await fireEvent.press(await screen.findByTestId('model-download-Want-GGUF'));
        await screen.findByText('Change this on the PC running GAIA.');
        expect(screen.getByTestId('model-download-Want-GGUF')).toBeTruthy();
        expect(client.modelDownloads).toEqual([]);
        queryClient.clear();
    });
});
