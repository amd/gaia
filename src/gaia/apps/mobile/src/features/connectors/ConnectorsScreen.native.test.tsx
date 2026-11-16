// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { PC_ONLY_REFUSAL } from '@/api/constants';
import { createFakeClient } from '@/api/fake';
import type { ConnectorRow, DisconnectOutcome } from '@/api/client.types';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import { ConnectorsScreen } from './ConnectorsScreen';

jest.mock('@/features/nav/ScreenHeader', () => ({ ScreenHeader: () => null }));
jest.mock('expo-router', () => ({ useRouter: () => ({ push: jest.fn(), back: jest.fn() }) }));

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const slack = {
    id: 'slack',
    display_name: 'Slack',
    type: 'mcp_server',
    description: 'Read channels.',
    configured: true,
    configurable: true,
    enabled: true,
    account_id: null,
    activations: {},
} as unknown as ConnectorRow;

function setup(connectors: ConnectorRow[], disconnectOutcome?: DisconnectOutcome) {
    const client = createFakeClient({ connectors, disconnectOutcome });
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

const microsoft = {
    id: 'microsoft',
    display_name: 'Microsoft',
    type: 'oauth_pkce',
    description: 'Mail and calendar.',
    configured: false,
    configurable: true,
    supports_device_code: true,
    oauth_setup_fields: [],
    account_id: null,
    activations: {},
} as unknown as ConnectorRow;

describeNative('ConnectorsScreen OAuth account', () => {
    it('sends Connect and Sign in with a code to the PC with phone wording', async () => {
        const { queryClient, wrap } = setup([microsoft]);
        await render(<ConnectorsScreen />, { wrapper: wrap });
        fireEvent.press(await screen.findByLabelText('Microsoft'));
        fireEvent.press(await screen.findByText('Sign in with a code'));
        expect(await screen.findByText(/Sign in with a code on your PC/)).toBeTruthy();
        fireEvent.press(await screen.findByText('Connect'));
        expect(await screen.findByText(/Connect this on your PC/)).toBeTruthy();
        queryClient.clear();
    });

    it('says when the provider did not confirm the revoke after a disconnect', async () => {
        const connected = { ...microsoft, configured: true, account_id: 'me@example.com' } as ConnectorRow;
        const { queryClient, wrap } = setup([connected], { revoke_supported: true, revoked_remotely: false, revoke_error: 'timeout' });
        await render(<ConnectorsScreen />, { wrapper: wrap });
        fireEvent.press(await screen.findByLabelText('Microsoft'));
        fireEvent.press(await screen.findByLabelText('Disconnect'));
        await screen.findByText(/^Disconnect Microsoft/);
        fireEvent.press((await screen.findAllByLabelText('Disconnect')).at(-1)!);
        expect(await screen.findByText(/did not confirm the revoke \(timeout\)/)).toBeTruthy();
        queryClient.clear();
    });
});

describeNative('ConnectorsScreen MCP server', () => {
    it('pauses a server from the phone, and refuses to turn it back on with the PC-only text', async () => {
        const { client, queryClient, wrap } = setup([slack]);
        await render(<ConnectorsScreen />, { wrapper: wrap });
        fireEvent.press(await screen.findByLabelText('Slack'));
        fireEvent.press(await screen.findByLabelText('Disable this MCP server'));
        await waitFor(async () => expect(((await client.listConnectors()).connectors[0] as ConnectorRow).enabled).toBe(false));

        fireEvent.press(await screen.findByText('Enable this MCP server'));
        expect(await screen.findByText(new RegExp(`^${PC_ONLY_REFUSAL.slice(0, 30)}`))).toBeTruthy();
        expect(((await client.listConnectors()).connectors[0] as ConnectorRow).enabled).toBe(false);
        queryClient.clear();
    });

    it('asks before Disconnect deletes the saved credentials', async () => {
        const { client, queryClient, wrap } = setup([slack]);
        await render(<ConnectorsScreen />, { wrapper: wrap });
        fireEvent.press(await screen.findByLabelText('Slack'));
        fireEvent.press(await screen.findByLabelText('Disconnect'));
        expect(await screen.findByText(/^Disconnect Slack\?/)).toBeTruthy();
        expect(((await client.listConnectors()).connectors[0] as ConnectorRow).configured).toBe(true);

        fireEvent.press((await screen.findAllByLabelText('Disconnect')).at(-1)!);
        await waitFor(async () => expect(((await client.listConnectors()).connectors[0] as ConnectorRow).configured).toBe(false));
        queryClient.clear();
    });
});
