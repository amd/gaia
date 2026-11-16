// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { SetupNpuProblem, SetupPlan } from '@webui/types';
import type { ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { GaiaApiError } from '@/api/errors';
import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';

import { NpuNotReady } from './NpuNotReady';

const memlock: SetupNpuProblem = { code: 'memlock', title: '', fix: '', params: { limit: '64 MB', fix: 'apply' } };
const driver: SetupNpuProblem = { code: 'driver', title: '', fix: '', params: {} };
const plan = { catalog: [] } as unknown as SetupPlan;

async function mount(client: ReturnType<typeof createFakeClient>, problems: SetupNpuProblem[], onReady = jest.fn()) {
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
    await render(<NpuNotReady problems={problems} onReady={onReady} />, { wrapper: wrap });
    return onReady;
}

describe('NpuNotReady', () => {
    it('names each problem with its fix, and shows Raise the limit read-only', async () => {
        await mount(createFakeClient({ npuRecheck: plan }), [driver, memlock]);
        expect(screen.getByText("The NPU driver (amdxdna) isn't loaded.")).toBeTruthy();
        expect(screen.getByText('The locked-memory limit is 64 MB, and the NPU needs it unlimited.')).toBeTruthy();
        expect(screen.getByText('Change this on the PC running GAIA')).toBeTruthy();
        expect(screen.getByLabelText('Raise the limit').props.accessibilityState).toMatchObject({ disabled: true });
    });

    it('offers no Raise the limit when the limit is not what is missing', async () => {
        await mount(createFakeClient({ npuRecheck: plan }), [driver]);
        expect(screen.queryByText('Raise the limit')).toBeNull();
    });

    it('Check again reads the NPU on the PC and hands the new plan on', async () => {
        const client = createFakeClient({ npuRecheck: plan });
        const onReady = await mount(client, [driver]);
        fireEvent.press(screen.getByText('Check again'));
        await waitFor(() => expect(onReady).toHaveBeenCalledWith(plan));
        expect(client.npuRechecks).toBe(1);
    });

    it('says so when the check fails, and sends a refused one to the PC', async () => {
        const client = createFakeClient({ npuRecheck: new GaiaApiError(503, 'down') });
        await mount(client, [driver]);
        fireEvent.press(screen.getByText('Check again'));
        await screen.findByText("GAIA couldn't check the NPU again. Make sure the model server is running, then try again.");
    });
});
