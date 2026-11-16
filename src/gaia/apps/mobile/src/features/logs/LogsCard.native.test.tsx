// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { useConnectionStore } from '@/state/connection';
import { ThemeProvider } from '@/ui';

import { LogsCard } from './LogsCard';

jest.mock('expo-router', () => ({ useRouter: () => ({ push: jest.fn() }) }));

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const wrap = ({ children }: { children: ReactNode }) => (
    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
        <ThemeProvider>{children}</ThemeProvider>
    </SafeAreaProvider>
);

describeNative('LogsCard', () => {
    afterEach(() => useConnectionStore.setState({ connection: null }));

    it('on a paired phone replaces View logs with the PC-only notice, and keeps the button unpaired', async () => {
        useConnectionStore.setState({ connection: { baseUrl: 'https://pc.local:4443', pcId: 'pc-1', pinned: true } });
        const pinned = await render(<LogsCard />, { wrapper: wrap });
        expect(screen.getByTestId('advanced-logs-pc-only')).toBeTruthy();
        expect(screen.queryByTestId('advanced-view-logs')).toBeNull();
        pinned.unmount();

        useConnectionStore.setState({ connection: null });
        await render(<LogsCard />, { wrapper: wrap });
        expect(screen.getByTestId('advanced-view-logs')).toBeTruthy();
        expect(screen.queryByTestId('advanced-logs-pc-only')).toBeNull();
    });
});
