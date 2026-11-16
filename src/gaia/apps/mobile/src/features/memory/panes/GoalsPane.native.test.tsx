// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { createFakeClient, type FakeClientData } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { SheetHost } from '@/ui/overlays/SheetHost';
import { ThemeProvider } from '@/ui';

import { SAMPLE_GOALS } from '../devScenes';
import { GoalsPane } from './GoalsPane';

// The web project's renderer can't mount react-native-web's DOM.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

async function mount(data: FakeClientData) {
    const client = createFakeClient(data);
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
    await render(<GoalsPane notify={() => undefined} memoryEnabled onOpenAdvanced={() => undefined} />, { wrapper: wrap });
    return client;
}

describeNative('GoalsPane', () => {
    it('accepts a suggestion, and deletes a goal only after a second tap', async () => {
        const client = await mount({ goals: SAMPLE_GOALS });
        await screen.findByTestId('goals-accept-g1');

        fireEvent.press(screen.getByTestId('goals-accept-g1'));
        await waitFor(() => expect(client.goalCalls).toEqual([{ action: 'approve', id: 'g1' }]));
        await waitFor(() => expect(screen.queryByTestId('goals-suggested')).toBeNull());

        fireEvent.press(screen.getByTestId('goal-toggle-g4'));
        fireEvent.press(await screen.findByTestId('goal-delete-g4'));
        expect(client.goalCalls).toHaveLength(1);
        fireEvent.press(await screen.findByTestId('goal-delete-confirm-g4'));
        await waitFor(() => expect(client.goalCalls[1]).toEqual({ action: 'delete', id: 'g4' }));
    });

    it('keeps Create off until the goal has a title, then sends it', async () => {
        const client = await mount({ goals: [] });
        await screen.findByTestId('goals-empty');
        fireEvent.press(screen.getByTestId('goals-new'));
        const create = await screen.findByTestId('goals-create-submit');
        expect(create.props.accessibilityState?.disabled).toBe(true);
        fireEvent.changeText(screen.getByTestId('goals-create-title'), '  Sort the receipts ');
        // The sheet shows what its owner last rendered, so press once the typed title has reached it.
        await waitFor(() => expect(screen.getByTestId('goals-create-title').props.value).toBe('  Sort the receipts '));
        fireEvent.press(screen.getByTestId('goals-create-submit'));
        await waitFor(() => expect(client.goalCalls).toEqual([{ action: 'create', title: 'Sort the receipts', description: '', priority: 'medium' }]));
    });

    it('says when approved goals are not running', async () => {
        await mount({ goals: [], goalStoreError: 'The goal list is unreadable.' });
        await screen.findByText('The goal list is unreadable.');
    });
});
