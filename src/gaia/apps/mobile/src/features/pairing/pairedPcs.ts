// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';

import { normalizeBaseUrl } from '@/api/http';
import { useConnectionStore } from '@/state/connection';

import { getPairingService } from './registry';
import type { PairedPc } from './service';

const PCS_KEY = ['pairing', 'pcs'] as const;

/** Read again whenever the PC in use changes: pairing another PC adds one, and the screen stays mounted in the drawer. */
export function usePairedPcs() {
    const baseUrl = useConnectionStore((s) => s.connection?.baseUrl) ?? null;
    return useQuery({ queryKey: [...PCS_KEY, baseUrl], queryFn: () => getPairingService().list(), placeholderData: keepPreviousData });
}

/** Whether the app is talking to this PC now. */
export function isCurrentPc(pc: PairedPc, baseUrl: string | undefined): boolean {
    return !!baseUrl && normalizeBaseUrl(pc.connection.baseUrl) === normalizeBaseUrl(baseUrl);
}

// Chats, skills and the rest belong to the PC that was in use.
function dropPcData(queryClient: QueryClient) {
    queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== 'pairing' });
}

export function useUsePc() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (pc: PairedPc) => {
            await getPairingService().use(pc.id);
            useConnectionStore.getState().setConnection(pc.connection);
        },
        onSuccess: () => {
            dropPcData(queryClient);
            return queryClient.invalidateQueries({ queryKey: PCS_KEY });
        },
    });
}

/** Forgetting the PC in use leaves no connection, so the app returns to the welcome screen. */
export function useForgetPc() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (pc: PairedPc) => {
            await getPairingService().forget(pc.id);
            if (isCurrentPc(pc, useConnectionStore.getState().connection?.baseUrl)) {
                useConnectionStore.getState().clearConnection();
                dropPcData(queryClient);
            }
        },
        onSuccess: () => queryClient.invalidateQueries({ queryKey: PCS_KEY }),
    });
}

/** After a pin mismatch: forget the PC in use, which sends the app back to the welcome screen. */
export function usePairAgain() {
    const forget = useForgetPc();
    return useMutation({
        mutationFn: async () => {
            const pcs = await getPairingService().list();
            const pc = pcs.find((p) => isCurrentPc(p, useConnectionStore.getState().connection?.baseUrl));
            if (pc) await forget.mutateAsync(pc);
            else useConnectionStore.getState().clearConnection();
        },
    });
}
