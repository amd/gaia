// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react-native';
import type { ReactNode } from 'react';

import { MODEL_STATUS_POLL_MS } from '@/api/constants';
import { GaiaApiError } from '@/api/errors';
import { createFakeClient } from '@/api/fake';

import { GaiaClientProvider } from './GaiaClientProvider';
import { useLocalModelStatus } from './models';
import { useQueryFailure } from './queries';

describe('the local model status', () => {
    beforeEach(() => jest.useFakeTimers());
    afterEach(() => jest.useRealTimers());

    it('keeps showing the failure, and keeps polling slowly, while the server is down', async () => {
        const client = createFakeClient({});
        const calls = jest.fn();
        client.getLocalModelStatus = async () => {
            calls();
            throw new GaiaApiError(502, 'The model server took no connection.');
        };
        const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const wrapper = ({ children }: { children: ReactNode }) => (
            <QueryClientProvider client={queryClient}>
                <GaiaClientProvider client={client}>{children}</GaiaClientProvider>
            </QueryClientProvider>
        );
        const hook = await renderHook(
            () => {
                const query = useLocalModelStatus(true);
                return { query, failure: useQueryFailure(query) };
            },
            { wrapper },
        );

        await act(async () => {
            await jest.advanceTimersByTimeAsync(100);
        });
        expect(hook.result.current.failure.failed).toBe(true);
        const first = calls.mock.calls.length;

        for (let i = 0; i < 3; i++) {
            await act(async () => {
                await jest.advanceTimersByTimeAsync(MODEL_STATUS_POLL_MS);
            });
            expect(hook.result.current.failure.failed).toBe(true);
            expect(hook.result.current.failure.error).toBeTruthy();
        }
        expect(calls.mock.calls.length).toBeGreaterThanOrEqual(first + 2);
        expect(calls.mock.calls.length).toBeLessThanOrEqual(first + 4);
        await hook.unmount();
        queryClient.clear();
    });
});
