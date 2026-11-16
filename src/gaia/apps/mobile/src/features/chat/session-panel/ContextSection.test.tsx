// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';

import { ContextSection } from './ContextSection';
import { useSessionPanelStore } from './panelStore';
import { SessionPanelGalleryData } from './SessionPanelGallery';

describe('the under-the-hood numbers', () => {
    it('show the last turn, then the whole chat, and a tap reveals the exact value', async () => {
        useSessionPanelStore.getState().setSection('context', true);
        useSessionPanelStore.getState().setContextScope('turn');
        const client = createFakeClient({ chatPerformance: SessionPanelGalleryData.performance });
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
        await render(<ContextSection sessionId="s1" live={false} />, { wrapper: wrap });
        expect(await screen.findByText('Took')).toBeTruthy();
        expect(screen.queryByText('Turns measured')).toBeNull();

        await fireEvent.press(screen.getByText('This chat'));
        expect(await screen.findByText('Turns measured')).toBeTruthy();
        expect(screen.queryByText('Took')).toBeNull();

        const total = screen.getByLabelText('96400 ms');
        await fireEvent.press(total);
        expect(screen.getByText(/96400 ms/)).toBeTruthy();
    });
});
