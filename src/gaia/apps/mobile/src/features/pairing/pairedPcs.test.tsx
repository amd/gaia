// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react-native';
import type { ReactNode } from 'react';

import { useConnectionStore } from '@/state/connection';

import { useForgetPc } from './pairedPcs';
import { setPairingService } from './registry';
import type { PairedPc, PairingService } from './service';

const pc = (id: string, baseUrl: string): PairedPc => ({ id, name: id, connection: { baseUrl } });

describe('useForgetPc', () => {
    afterEach(() => setPairingService(null));

    it('leaves no connection only when the PC in use is the one forgotten', async () => {
        const forget = jest.fn(async () => {});
        setPairingService({ forget } as unknown as PairingService);
        const wrapper = ({ children }: { children: ReactNode }) => (
            <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
        );
        const { result } = await renderHook(() => useForgetPc(), { wrapper });

        useConnectionStore.getState().setConnection({ baseUrl: 'https://192.168.1.20:4243' });
        await act(async () => { await result.current.mutateAsync(pc('other', 'https://192.168.1.30:4243')); });
        expect(useConnectionStore.getState().connection).not.toBeNull();

        await act(async () => { await result.current.mutateAsync(pc('mine', 'https://192.168.1.20:4243')); });
        expect(forget).toHaveBeenCalledTimes(2);
        expect(useConnectionStore.getState().connection).toBeNull();
    });
});
