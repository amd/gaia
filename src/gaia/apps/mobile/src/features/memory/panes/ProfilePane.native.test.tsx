// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { createFakeClient, type FakeClientData } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { useConnectionStore } from '@/state/connection';
import { ThemeProvider } from '@/ui';

import { ProfilePane } from './ProfilePane';

// The web project's renderer can't mount react-native-web's DOM.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

async function mount(data: FakeClientData) {
    const client = createFakeClient(data);
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
    await render(<ProfilePane notify={() => undefined} memoryEnabled onOpenAdvanced={() => undefined} />, { wrapper: wrap });
    return client;
}

const finding = (content: string) => ({ type: 'finding' as const, source: 'installed_apps', item: { content, category: 'profile', context: 'global', confidence: 0.8, _source_name: 'installed_apps' } });

describeNative('ProfilePane', () => {
    it('saves nothing until the review is confirmed, and only what is left checked', async () => {
        const client = await mount({
            profileStreams: { discovery: { events: [finding('Uses Firefox'), finding('Uses Blender'), { type: 'done', total: 2 }] } },
        });
        fireEvent.press(screen.getByTestId('profile-discovery-run'));
        await screen.findByTestId('profile-discovery-review');
        expect(client.profileCommits).toEqual([]);

        fireEvent.press(screen.getByLabelText(/^Uses Blender/));
        await waitFor(() => expect(screen.getByLabelText(/^Uses Blender/).props.accessibilityState.checked).toBe(false));
        fireEvent.press(screen.getByTestId('profile-discovery-save'));
        await waitFor(() => expect(client.profileCommits).toHaveLength(1));
        expect(client.profileCommits[0]!.items).toEqual([expect.objectContaining({ content: 'Uses Firefox' })]);
    });

    it('aborts the stream when Stop is pressed, and keeps the browser history off by default', async () => {
        const client = await mount({ profileStreams: { inference: { events: [{ type: 'log', message: 'Calling local LLM...' }], hang: true } } });
        fireEvent.press(screen.getByTestId('profile-inference-run'));
        const stop = await screen.findByTestId('profile-inference-stop');
        expect(client.profileStreamOpens).toEqual([{ kind: 'inference', includeBrowser: false, stopped: false }]);
        fireEvent.press(stop);
        await waitFor(() => expect(client.profileStreamOpens[0]!.stopped).toBe(true));
        await waitFor(() => expect(screen.queryByTestId('profile-inference-stop')).toBeNull());
    });

    it('says the scan runs on the PC and offers no Run on a paired phone', async () => {
        useConnectionStore.setState({ connection: { baseUrl: 'https://pc.local:4443', pcId: 'pc-1', pinned: true } });
        try {
            const client = await mount({});
            expect(screen.getByTestId('profile-discovery-pc-only')).toBeTruthy();
            expect(screen.getByTestId('profile-inference-pc-only')).toBeTruthy();
            expect(screen.queryByTestId('profile-discovery-run')).toBeNull();
            expect(screen.queryByTestId('profile-inference-run')).toBeNull();
            expect(client.profileStreamOpens).toEqual([]);
        } finally {
            useConnectionStore.setState({ connection: null });
        }
    });
});
