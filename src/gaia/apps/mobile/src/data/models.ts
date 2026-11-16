// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { LocalModelStatus, ModelNeedJob, RestartModelResult } from '@/api/client.types';
import { MODEL_STATUS_OPEN_POLL_MS, MODEL_STATUS_POLL_MS } from '@/api/constants';
import { useModelNeededStore } from '@/state/modelNeeded';

import { useGaiaClient } from './GaiaClientProvider';
import { failedWithoutData, requireClient } from './queries';

export const modelKeys = {
    local: ['localModelStatus'] as const,
};

/** The model server's state, placement and memory. Polls faster while `watching` (a sheet showing it is open). */
export function useLocalModelStatus(watching = false) {
    const client = useGaiaClient();
    return useQuery<LocalModelStatus>({
        queryKey: modelKeys.local,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getLocalModelStatus({ signal }),
        // Keep polling after a failure, slowly: it is how the "server is down" notice sees recovery.
        refetchInterval: (query) =>
            watching && !failedWithoutData(query) ? MODEL_STATUS_OPEN_POLL_MS : MODEL_STATUS_POLL_MS,
    });
}

/**
 * Starts the model server if it is stopped, then reloads the chat model: `mutate({ confirm })`.
 * `confirm` agrees to reload a Lemonade GAIA doesn't own. Rejects with a 409 `GaiaApiError` while the model is busy.
 */
export function useRestartLocalModel() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<RestartModelResult, Error, { confirm: boolean }>({
        mutationFn: ({ confirm }) => requireClient(client).restartLocalModel(confirm),
        onSettled: () => queryClient.invalidateQueries({ queryKey: modelKeys.local }),
    });
}

/**
 * Resolves whether a job can run: true at once when its model is on the PC, else asks to download it
 * (the model-needed dialog) and resolves with the answer. `ensure('chat')` before a send.
 */
export function useEnsureModel(): (job: ModelNeedJob) => Promise<boolean> {
    const client = useGaiaClient();
    return useCallback((job) => useModelNeededStore.getState().ensure(requireClient(client), job), [client]);
}
