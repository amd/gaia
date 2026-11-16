// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { shortModelName } from '@webui/utils/modelList.core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { ActiveModel, PermissionMode, ProviderModel, ProviderModelsResponse, SessionPermissions } from '@/api/client.types';
import { DOWNLOAD_POLL_MS } from '@/api/constants';

import { useGaiaClient } from './GaiaClientProvider';
import { modelKeys } from './models';
import { queryKeys, requireClient } from './queries';

export const modelMenuKeys = {
    active: ['activeModel'] as const,
    models: (provider: string) => ['providerModels', provider] as const,
    permissions: (sessionId: string) => ['permissions', sessionId] as const,
    retry: (cloud: string | null) => ['retryChoices', cloud] as const,
};

/** The model new messages use and whether it runs on this PC. */
export function useActiveModel() {
    const client = useGaiaClient();
    return useQuery<ActiveModel>({
        queryKey: modelMenuKeys.active,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getActiveModel({ signal }),
    });
}

/**
 * One provider's models; polled while `watching` so a download shows its progress.
 * A failure with no list yet stops the poll, so the error stays shown; reopening tries again.
 */
export function useProviderModels(provider: string | undefined, watching: boolean) {
    const client = useGaiaClient();
    return useQuery<ProviderModelsResponse>({
        queryKey: modelMenuKeys.models(provider ?? ''),
        enabled: client !== null && !!provider && watching,
        queryFn: ({ signal }) => requireClient(client).listProviderModels(provider!, { signal }),
        // Each poll would put a failed query with no data back to pending, showing "Loading" for good.
        refetchInterval: (query) =>
            watching && !(query.state.status === 'error' && query.state.data === undefined) ? DOWNLOAD_POLL_MS : false,
    });
}

/** Switches the model: `mutate(modelId)`. */
export function useSelectModel() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<ActiveModel, Error, string>({
        mutationFn: (model) => requireClient(client).selectModel(model),
        onSettled: () => {
            void queryClient.invalidateQueries({ queryKey: modelMenuKeys.active });
            void queryClient.invalidateQueries({ queryKey: modelKeys.local });
            void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
        },
    });
}

/** Starts a model's download on the PC: `mutate(modelId)`. Its progress shows in the model list. */
export function useDownloadModel() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<unknown, Error, string>({
        mutationFn: (modelId) => requireClient(client).downloadSetupModel(modelId, false),
        onSettled: () => queryClient.invalidateQueries({ queryKey: ['providerModels'] }),
    });
}

/** The chat's permission mode and a way to change it. */
export function usePermissionMode(sessionId: string | null) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const query = useQuery<SessionPermissions>({
        queryKey: modelMenuKeys.permissions(sessionId ?? ''),
        enabled: client !== null && !!sessionId,
        queryFn: ({ signal }) => requireClient(client).getPermissions(sessionId!, { signal }),
    });
    const set = useMutation<SessionPermissions, Error, PermissionMode>({
        mutationFn: (mode) => requireClient(client).setPermissionMode(sessionId!, mode),
        onSuccess: (saved) => queryClient.setQueryData(modelMenuKeys.permissions(sessionId!), saved),
    });
    return { mode: query.data?.mode, set };
}

export interface RetryModel {
    id: string;
    name: string;
    sizeGb: number | null;
}

export interface RetryChoices {
    /** The other models of the cloud service in use; empty on a local chat. */
    cloud: RetryModel[];
    /** Downloaded models on this PC, from every local provider. */
    local: RetryModel[];
}

const toRetryModel = (m: ProviderModel): RetryModel => ({ id: m.id, name: m.display_name || shortModelName(m.id), sizeGb: m.size_gb ?? null });

/** The models "Retry with another model" offers: the cloud service this chat runs on, then what is downloaded on this PC. */
export function useRetryChoices(open: boolean, cloudProvider: string | null) {
    const client = useGaiaClient();
    return useQuery<RetryChoices>({
        queryKey: modelMenuKeys.retry(cloudProvider),
        enabled: client !== null && open,
        gcTime: 0,
        queryFn: async ({ signal }) => {
            const c = requireClient(client);
            const { providers: listed, lemonade_error } = await c.listProviderSummaries({ signal });
            if (lemonade_error) throw new Error(lemonade_error);
            const providers = listed.filter((p) => p.configured && !p.blocked);
            const inUse = cloudProvider ? providers.find((p) => p.remote && p.id === cloudProvider) : undefined;
            const [cloudList, ...lists] = await Promise.all([
                inUse ? c.listProviderModels(inUse.id, { signal }) : Promise.resolve(null),
                ...providers.filter((p) => !p.remote).map((p) => c.listProviderModels(p.id, { signal })),
            ]);
            return {
                cloud: cloudList && !cloudList.blocked ? cloudList.models.filter((m) => m.available !== false).map((m) => ({ ...toRetryModel(m), sizeGb: null })) : [],
                local: lists.flatMap((l) => l.models.filter((m) => m.downloaded).map(toRetryModel)),
            };
        },
    });
}
