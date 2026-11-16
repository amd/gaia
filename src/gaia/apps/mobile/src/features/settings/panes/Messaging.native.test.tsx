// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';
import * as Clipboard from 'expo-clipboard';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import type { DiscordStatus } from '@/api/client.types';
import { PC_ONLY_REFUSAL } from '@/api/constants';
import { GaiaApiError } from '@/api/errors';
import { createFakeClient, type FakeClientData } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import Messaging from './Messaging';

jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(() => Promise.resolve(true)) }));

// The web project's renderer can't mount react-native-web's DOM or the sheet host.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

const PAIRING: DiscordStatus = {
    connected: true,
    paired: false,
    running: true,
    state: 'pairing',
    detail: '',
    bot_name: 'GAIA test bot',
    owner_name: '',
    autostart: true,
    always_allowed: [],
    model: null,
    portal_url: 'https://example.invalid/portal',
    bot_page_url: 'https://example.invalid/bot',
    invite_url: 'https://example.invalid/invite',
    bot_profile_url: 'https://example.invalid/profile',
    pairing_code: 'DUMY2345',
    pairing_expires_at: 0,
    discord_installed: false,
    should_offer_setup: false,
};

async function renderPane(discordStatus: FakeClientData['discordStatus']) {
    const client = createFakeClient({ discordStatus });
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
    await render(<Messaging />, { wrapper: wrap });
    return queryClient;
}

// Any input that could take the bot token: none may render, whatever the PC answers.
const secretFields = () => screen.container.queryAll((node) => node.props.secureTextEntry === true || node.type === 'TextInput');

describeNative('Messaging pane', () => {
    it("the PC's 403 shows the PC-only notice and never a token field", async () => {
        const queryClient = await renderPane(new GaiaApiError(403, PC_ONLY_REFUSAL));
        await screen.findByTestId('messaging-pc-only');
        expect(screen.getByText('Change this on the PC running GAIA.')).toBeTruthy();
        expect(secretFields()).toHaveLength(0);
        expect(screen.queryByPlaceholderText('Paste the bot token')).toBeNull();
        fireEvent.press(screen.getByTestId('messaging-connect'));
        expect(await screen.findByTestId('messaging-connect-refusal')).toBeTruthy();
        expect(secretFields()).toHaveLength(0);
        queryClient.clear();
    });

    it('a pairing code the PC sends shows, and Copy puts the raw code on the clipboard', async () => {
        const queryClient = await renderPane(PAIRING);
        await screen.findByText('DUMY-2345', { exact: false });
        fireEvent.press(screen.getByTestId('messaging-copy-code'));
        await screen.findByText('Copied');
        expect(Clipboard.setStringAsync).toHaveBeenCalledWith('DUMY2345');
        expect(secretFields()).toHaveLength(0);
        queryClient.clear();
    });
});
