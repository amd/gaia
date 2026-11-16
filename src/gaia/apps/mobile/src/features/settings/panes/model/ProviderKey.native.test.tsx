// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider, type ProviderRow } from '@/data';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import { ProviderKey } from './ProviderKey';

// The web project's renderer can't mount react-native-web's DOM or the sheet host.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const DUMMY_KEY = 'sk-test-dummy';

function setup(provider: ProviderRow) {
    const client = createFakeClient({ providers: { providers: [provider], lemonade_error: null } });
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
    return { client, queryClient, wrap };
}

describeNative('Provider key', () => {
    it('never offers a key field the PC refuses from a phone: no input, no gateway fields, the PC-only notice', async () => {
        const none: ProviderRow = { id: 'amd', name: 'AMD LLM Gateway', remote: true, privacy_notice: '', configured: false, blocked: false };
        const { queryClient, wrap } = setup(none);
        const r = await render(<ProviderKey provider={none} connected={false} />, { wrapper: wrap });

        expect(screen.getByTestId('provider-key-pc-only')).toHaveTextContent('Change this on the PC running GAIA.');
        expect(JSON.stringify(r.container.toJSON())).not.toContain('"type":"TextInput"');
        expect(screen.queryByText(/Gateway URL|Auth header/)).toBeNull();
        await fireEvent.press(screen.getByTestId('provider-key-paste'));
        expect(await screen.findByTestId('provider-key-paste-refusal')).toBeTruthy();
        expect(JSON.stringify(r.container.toJSON())).not.toContain('"type":"TextInput"');
        queryClient.clear();
    });

    it('Reconnect works without any key leaving the phone', async () => {
        const saved: ProviderRow = { id: 'fireworks', name: 'Fireworks AI', remote: true, privacy_notice: '', configured: false, blocked: false };
        const { client, queryClient, wrap } = setup(saved);
        const r = await render(<ProviderKey provider={saved} connected={false} />, { wrapper: wrap });

        expect(screen.getByTestId('provider-key-pc-only')).toBeTruthy();
        expect(screen.queryByText(/^(Replace|Remove key)$/)).toBeNull();

        await fireEvent.press(screen.getByText('Reconnect'));
        await screen.findByText('Connected. The key is kept for next time.');
        expect(client.connects).toEqual(['fireworks']);
        const shown = JSON.stringify(r.container.toJSON());
        expect(shown).not.toContain('"type":"TextInput"');
        expect(shown).not.toContain(DUMMY_KEY);
        queryClient.clear();
    });
});
