// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { useConnectionStore } from '@/state/connection';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import { MemoryAdmin } from './MemoryAdmin';

// The web project's renderer can't mount react-native-web's DOM or the sheet host.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

describeNative('MemoryAdmin', () => {
    it('Delete all asks twice, then deletes once', async () => {
        const settings = { memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false };
        const client = createFakeClient({ memorySettings: settings });
        const notify = jest.fn();
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
        await render(<MemoryAdmin settings={settings} notify={notify} />, { wrapper: wrap });

        fireEvent.press(screen.getByTestId('memory-delete'));
        await screen.findByText('Are you sure?');
        expect(client.memoryWipes.clears).toBe(0);

        fireEvent.press(screen.getByTestId('memory-wipe-confirm'));
        await waitFor(() => expect(client.memoryWipes.clears).toBe(1));
        await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.objectContaining({ tone: 'info' })));
        expect(client.memoryWipes.reinitializes).toBe(0);
        queryClient.clear();
    });

    it('on a paired phone, locks discovery-on and Reset (the scan is PC-only) but still lets consent be turned off', async () => {
        useConnectionStore.setState({ connection: { baseUrl: 'https://pc.local:4443', pcId: 'pc-1', pinned: true } });
        try {
            const off = { memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false };
            const client = createFakeClient({ memorySettings: off });
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
            const view = await render(<MemoryAdmin settings={off} notify={jest.fn()} />, { wrapper: wrap });
            expect(screen.getByTestId('memory-discovery-pc-only')).toBeTruthy();
            expect(screen.getByTestId('memory-reinit-pc-only')).toBeTruthy();
            expect(screen.getByTestId('memory-switch-discovery').props.accessibilityState?.disabled).toBe(true);
            expect(screen.getByTestId('memory-reinit').props.accessibilityState?.disabled).toBe(true);

            await view.rerender(<MemoryAdmin settings={{ ...off, system_discovery_consent: true }} notify={jest.fn()} />);
            expect(screen.queryByTestId('memory-discovery-pc-only')).toBeNull();
            expect(screen.getByTestId('memory-switch-discovery').props.accessibilityState?.disabled).not.toBe(true);
            queryClient.clear();
        } finally {
            useConnectionStore.setState({ connection: null });
        }
    });
});
