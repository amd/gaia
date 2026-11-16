// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { router } from 'expo-router';
import type { ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import type { SetupRunStatus } from '@/api/client.types';
import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';

import type { SetupStepContext } from '../SetupScreen';
import { ReadyStep } from './ReadyStep';

jest.mock('expo-router', () => ({ router: { replace: jest.fn() } }));

const run: SetupRunStatus = { state: 'ready', chat_model: 'Gemma-4-E4B-it-GGUF' };

async function mount(finish: () => void) {
    const client = createFakeClient({
        activeModel: { provider: 'local', model: 'old', display_name: 'old', label: null, remote: false, is_default: true, downloaded: true },
        start: {
            cards: [{ skill: 'summarize', title: 'Summarize', note: 'Any text', prompt: 'Summarize this', needs_input: true }],
            working_folder: { path: '/home/test', name: 'test', gaia_folder: false, choosable: true },
        },
    });
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
    const ctx: SetupStepContext = { stage: 'ready', run, plan: undefined, locked: false, again: false, goTo: () => undefined, startedRun: () => undefined, finish };
    await render(<ReadyStep ctx={ctx} />, { wrapper: wrap });
    return client;
}

describe('ReadyStep', () => {
    it('selects the tested model, then finishes setup', async () => {
        const finish = jest.fn();
        const client = await mount(finish);
        fireEvent.press(screen.getByText('Start chatting'));
        await waitFor(() => expect(finish).toHaveBeenCalledTimes(1));
        expect(client.selected).toEqual(['Gemma-4-E4B-it-GGUF']);
        expect(router.replace).not.toHaveBeenCalled();
    });

    it('a starter card finishes setup and opens a new chat with its prompt', async () => {
        const finish = jest.fn();
        await mount(finish);
        fireEvent.press(await screen.findByText('Summarize'));
        await waitFor(() => expect(finish).toHaveBeenCalledTimes(1));
        expect(router.replace).toHaveBeenCalledWith({ pathname: '/new', params: expect.objectContaining({ prompt: 'Summarize this' }) });
    });
});
