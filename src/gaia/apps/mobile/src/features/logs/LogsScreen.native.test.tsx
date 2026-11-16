// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { ThemeProvider } from '@/ui';

import { LogsScreen } from './LogsScreen';

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

describeNative('LogsScreen', () => {
    it('shows the PC-only refusal, not data, when a log action is pressed', async () => {
        const wrap = ({ children }: { children: ReactNode }) => (
            <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
                <ThemeProvider>{children}</ThemeProvider>
            </SafeAreaProvider>
        );
        await render(<LogsScreen onBack={() => {}} />, { wrapper: wrap });
        expect(screen.queryByTestId('logs-save-all-refusal')).toBeNull();

        fireEvent.press(screen.getByTestId('logs-save-all'));
        expect(await screen.findByText(/^Do this on your PC\./)).toBeTruthy();
        fireEvent.press(screen.getByTestId('logs-report'));
        expect(await screen.findByTestId('logs-report-refusal')).toBeTruthy();
    });
});
