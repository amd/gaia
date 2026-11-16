// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import Permissions from './Permissions';

// The web project's renderer can't mount react-native-web's DOM or the sheet host.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

describeNative('Permissions pane', () => {
    it('a 403 on the default-mode switch leaves it off and shows the translated PC-only notice', async () => {
        const client = createFakeClient({ defaultPermissionMode: 'ask', defaultModePcOnly: true });
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
        await render(<Permissions />, { wrapper: wrap });
        await screen.findByText(/^Off\. New chats ask/);

        fireEvent.press(screen.getByTestId('permissions-default-switch'));
        fireEvent.press(await screen.findByText('Turn on full access for new chats'));

        await screen.findByText('Change this on the PC running GAIA.');
        expect(screen.getByTestId('permissions-default-switch').props.accessibilityState).toMatchObject({ checked: false });
        expect((await client.listAllPermissions()).default_mode).toBe('ask');
        queryClient.clear();
    });
});
