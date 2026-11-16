// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react-native';
import type { ReactNode } from 'react';

import { DOWNLOAD_POLL_MS } from '@/api/constants';
import { GaiaApiError } from '@/api/errors';
import { createFakeClient } from '@/api/fake';

import { GaiaClientProvider } from './GaiaClientProvider';
import { useProviderModels } from './modelMenu';

describe('the model menu list', () => {
    beforeEach(() => jest.useFakeTimers());
    afterEach(() => jest.useRealTimers());

    it('keeps a failed list failed while the menu stays open, instead of loading again on every poll', async () => {
        const client = createFakeClient({});
        const calls = jest.fn();
        client.listProviderModels = async () => {
            calls();
            throw new GaiaApiError(502, 'Lemonade answered /system-info with HTTP 404.');
        };
        // The app's own retry default, so the poll and the retries race as they do on a phone.
        const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 3 } } });
        const wrapper = ({ children }: { children: ReactNode }) => (
            <QueryClientProvider client={queryClient}>
                <GaiaClientProvider client={client}>{children}</GaiaClientProvider>
            </QueryClientProvider>
        );
        const menu = await renderHook(() => useProviderModels('local', true), { wrapper });

        await act(async () => {
            await jest.advanceTimersByTimeAsync(30_000);
        });
        expect(menu.result.current.isError).toBe(true);
        expect(menu.result.current.isLoading).toBe(false);
        const settled = calls.mock.calls.length;
        await act(async () => {
            await jest.advanceTimersByTimeAsync(DOWNLOAD_POLL_MS * 10);
        });
        expect(calls).toHaveBeenCalledTimes(settled);
        expect(menu.result.current.isError).toBe(true);
        await menu.unmount();
        queryClient.clear();
    });
});
