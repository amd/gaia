// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { createFakeClient } from '@/api/fake';
import { GaiaApiError } from '@/api/errors';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';

import { SAMPLE_MEMORY_STATS } from './devScenes';
import { MemoryScreen } from './MemoryScreen';

async function mount(client: ReturnType<typeof createFakeClient>) {
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
    await render(<MemoryScreen onBack={() => undefined} />, { wrapper: wrap });
}

describe('MemoryScreen', () => {
    it('shows the failed state when the stats call fails, and Retry reads them again', async () => {
        const client = createFakeClient({
            memorySettings: { memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false },
            memoryStats: [new GaiaApiError(503, 'The backend is not running'), SAMPLE_MEMORY_STATS],
        });
        await mount(client);
        fireEvent.press(screen.getByTestId('memory-tab-advanced'));

        await screen.findByText('Failed to load memory data. Is the backend running?');
        fireEvent.press(screen.getByText('Retry'));

        await screen.findByTestId('memory-pane-advanced');
        expect(client.memoryStatsReads).toBe(2);
    });

    it('reads the activity for the range that was picked', async () => {
        const client = createFakeClient({
            memorySettings: { memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false },
            memoryStats: [SAMPLE_MEMORY_STATS],
            memoryActivity: () => [],
            memoryTools: [],
            memoryUpcoming: [],
        });
        await mount(client);
        fireEvent.press(screen.getByTestId('memory-tab-advanced'));
        await screen.findByTestId('memory-pane-advanced');
        expect(client.memoryActivityReads).toEqual([30]);

        fireEvent.press(screen.getByText('7D'));
        await screen.findByText('No activity data yet');
        expect(client.memoryActivityReads).toEqual([30, 7]);
    });
});
