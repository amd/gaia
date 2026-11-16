// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { ChatSpending, SpendingReport } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { requireClient } from './queries';

export const spendingKeys = {
    all: ['spending'] as const,
    chat: (sessionId: string, model: string | null) => ['chatSpending', sessionId, model] as const,
};

/** "$0.04"; a share of a cent keeps four places so it never reads as "$0.00". */
export function formatUsd(usd: number): string {
    return `$${usd > 0 && usd < 0.01 ? usd.toFixed(4) : usd.toFixed(2)}`;
}

/** The start of the viewer's local day, as the instant the PC compares against. */
export function startOfLocalDay(now: Date = new Date()): string {
    const day = new Date(now);
    day.setHours(0, 0, 0, 0);
    return day.toISOString();
}

/** This chat's and today's cloud spending for `model`; read again as each reply ends. */
export function useChatSpending(sessionId: string, model: string | null, { busy, enabled }: { busy: boolean; enabled: boolean }) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const key = spendingKeys.chat(sessionId, model);
    const wasBusy = useRef(busy);
    useEffect(() => {
        if (wasBusy.current && !busy) void queryClient.invalidateQueries({ queryKey: key });
        wasBusy.current = busy;
    }, [busy, queryClient, key]);
    return useQuery<ChatSpending>({
        queryKey: key,
        enabled: client !== null && enabled,
        refetchOnReconnect: true,
        queryFn: ({ signal }) => requireClient(client).getChatSpending(sessionId, startOfLocalDay(), model, { signal }),
    });
}

/** This month's cloud spending at list price, the monthly limit and where it stands. */
export function useSpending() {
    const client = useGaiaClient();
    return useQuery<SpendingReport>({
        queryKey: spendingKeys.all,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getSpending({ signal }),
    });
}

/** `mutate(limitUsd)`; null removes the limit. */
export function useSetSpendingLimit() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<SpendingReport, Error, number | null>({
        mutationFn: (limitUsd) => requireClient(client).setSpendingLimit(limitUsd),
        onSuccess: (report) => queryClient.setQueryData(spendingKeys.all, report),
    });
}

/** Lets cloud chats go on past the limit until the month ends. */
export function useAllowSpendingMonth() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<SpendingReport, Error, void>({
        mutationFn: () => requireClient(client).allowSpendingThisMonth(),
        onSuccess: (report) => queryClient.setQueryData(spendingKeys.all, report),
    });
}
