// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { CloudPriceList, ContextChoice, ProviderConnected, ProviderId, ProviderSummary, ProviderSummaryList } from '@/api/client.types';
import { CONNECT_RETRY_MS } from '@/api/constants';
import { GaiaApiError } from '@/api/errors';

import { useGaiaClient } from './GaiaClientProvider';
import { modelMenuKeys } from './modelMenu';
import { modelKeys } from './models';
import { requireClient } from './queries';

export const providerKeys = {
    // Shared with setup's useCloudProviders, so a Local-only change refreshes both.
    all: ['cloudProviders'] as const,
    context: (model: string) => ['contextChoice', model] as const,
    prices: (provider: string) => ['cloudPrices', provider] as const,
};

export interface ProviderRow extends Omit<ProviderSummary, 'id'> {
    id: ProviderId;
}

/** The PC's provider list as rows; an id this app has no row for is left out. */
export function providerRows(list: ProviderSummaryList): { providers: ProviderRow[]; lemonadeError: string | null } {
    const known: readonly string[] = ['local', 'fireworks', 'amd'];
    const providers = list.providers.filter((p): p is ProviderRow => known.includes(p.id));
    return { providers, lemonadeError: list.lemonade_error ?? null };
}

/** Every provider with whether it is set up and whether Local only holds it off. */
export function useProviders() {
    const client = useGaiaClient();
    return useQuery({
        queryKey: providerKeys.all,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).listProviderSummaries({ signal }),
        select: providerRows,
    });
}

export function useContextChoice(model: string, enabled: boolean) {
    const client = useGaiaClient();
    return useQuery<ContextChoice>({
        queryKey: providerKeys.context(model),
        enabled: client !== null && enabled,
        queryFn: ({ signal }) => requireClient(client).getContextChoice(model, { signal }),
    });
}

/** `mutate({ model, ctxSize })`; a null size returns the model to Automatic. */
export function useSetContextChoice() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<ContextChoice, Error, { model: string; ctxSize: number | null }>({
        mutationFn: ({ model, ctxSize }) => requireClient(client).setContextChoice(model, ctxSize),
        onSuccess: (choice, { model }) => {
            queryClient.setQueryData(providerKeys.context(model), choice);
            void queryClient.invalidateQueries({ queryKey: modelKeys.local });
        },
    });
}

export function useCloudPrices(provider: string, enabled: boolean) {
    const client = useGaiaClient();
    return useQuery<CloudPriceList>({
        queryKey: providerKeys.prices(provider),
        enabled: client !== null && enabled,
        queryFn: ({ signal }) => requireClient(client).getCloudPrices(provider, { signal }),
    });
}

function useRefreshProviders() {
    const queryClient = useQueryClient();
    return () => {
        void queryClient.invalidateQueries({ queryKey: providerKeys.all });
        void queryClient.invalidateQueries({ queryKey: modelMenuKeys.active });
        void queryClient.invalidateQueries({ queryKey: ['providerModels'] });
    };
}

/**
 * Reconnects a provider with the key the PC already holds; no key leaves the phone. While the PC's OS
 * holds the key read for permission (409) it asks again, telling `onWaiting`, until `stopped()`.
 */
export function useReconnectProvider() {
    const client = useGaiaClient();
    const refresh = useRefreshProviders();
    return useMutation<
        ProviderConnected,
        Error,
        { provider: string; onWaiting: (detail: string) => void; stopped: () => boolean }
    >({
        mutationFn: async ({ provider, onWaiting, stopped }) => {
            const api = requireClient(client);
            for (;;) {
                try {
                    return await api.connectProvider(provider);
                } catch (err) {
                    if (!(err instanceof GaiaApiError) || err.status !== 409 || stopped()) throw err;
                    onWaiting(err.detail);
                    await new Promise((resolve) => setTimeout(resolve, CONNECT_RETRY_MS));
                }
            }
        },
        onSettled: refresh,
    });
}

/** After the PC's key-store unlock prompt was closed: GAIA asks once more on the PC, then the list is read again. */
export function useRetryKeyStore() {
    const client = useGaiaClient();
    const refresh = useRefreshProviders();
    return useMutation<{ retrying: boolean }, Error, void>({
        mutationFn: () => requireClient(client).retryKeyStore(),
        onSettled: refresh,
    });
}
