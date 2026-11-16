// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { CloudPriceList } from '@webui/types';
import type { ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import type { ProviderSummary } from '@/api/client.types';
import type { FakeClientData } from '@/api/fake';
import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';

import type { SetupStepContext } from '../SetupScreen';
import { KeyScreen } from './KeyScreen';

const provider = (p: Partial<ProviderSummary>): ProviderSummary => ({ id: 'fireworks', name: 'Fireworks AI', remote: true, privacy_notice: '', configured: false, blocked: false, ...p });
const prices: CloudPriceList = {
    provider: 'fireworks', source: 'fireworks.ai/pricing', note: null,
    models: [
        { model: 'fireworks.kimi', name: 'Kimi', rank: 1, note: null, price: { input_per_million: 0.6, output_per_million: 2.5 } as never },
        { model: 'fireworks.glm', name: 'GLM', rank: 2, note: null, price: { input_per_million: 0.6, output_per_million: 2.5 } as never },
    ],
};

async function mount(providers: ProviderSummary[], extra: FakeClientData = {}) {
    const client = createFakeClient({
        providers: { providers, lemonade_error: null },
        cloudPrices: { fireworks: prices },
        providerModels: { fireworks: { provider: 'fireworks', models: [{ id: 'fireworks.kimi', rank: 1 } as never, { id: 'fireworks.glm', rank: 2 } as never] } },
        activeModel: { provider: 'local', model: 'm', remote: false } as never,
        lemonadeTarget: { source: 'gaia', offer: false } as never,
        ...extra,
    });
    const ctx = { locked: false, startedRun: jest.fn() } as unknown as SetupStepContext;
    const close = jest.fn();
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
    await render(<KeyScreen ctx={ctx} close={close} />, { wrapper: wrap });
    return { client, ctx, close };
}

describe('KeyScreen', () => {
    it('without a key on the PC, shows the key field read-only and cannot connect', async () => {
        const { client } = await mount([provider({ configured: false })]);
        await screen.findByText('Change this on the PC running GAIA');
        expect(screen.getByLabelText('Test and connect').props.accessibilityState).toMatchObject({ disabled: true });
        expect(client.connects).toEqual([]);
    });

    it('with a key on the PC, connects it, selects the model and starts the cloud run, sending no key', async () => {
        const { client, ctx } = await mount([provider({ configured: true })]);
        await screen.findByText('Kimi · Default', { exact: false });
        fireEvent.press(screen.getByLabelText('Test and connect'));
        await waitFor(() => expect(ctx.startedRun).toHaveBeenCalled());
        expect(client.connects).toEqual(['fireworks']);
        expect(client.setupStarts).toMatchObject([{ skipChatModel: true }]);
    });

    it('run again with a cloud model in use keeps that model and connects it, not the top-ranked one', async () => {
        const { client, ctx } = await mount([provider({ configured: true })], {
            activeModel: { provider: 'fireworks', model: 'fireworks.glm', remote: true } as never,
        });
        await screen.findByText('GLM', { exact: false });
        fireEvent.press(screen.getByLabelText('Test and connect'));
        await waitFor(() => expect(ctx.startedRun).toHaveBeenCalled());
        expect(client.selected).toEqual(['fireworks.glm']);
    });

    it('with the AMD gateway unreachable and already chosen, says so and offers to check again', async () => {
        const { client } = await mount([provider({ id: 'amd', name: 'AMD LLM Gateway', network: { reachable: false } })], { activeModel: { provider: 'amd', model: 'm', remote: true } as never });
        await screen.findByText("Unavailable: needs AMD's network or VPN.");
        const reads = jest.spyOn(client, 'listProviderSummaries');
        fireEvent.press(screen.getByLabelText('Check the AMD gateway again'));
        await waitFor(() => expect(reads).toHaveBeenCalled());
    });
});

