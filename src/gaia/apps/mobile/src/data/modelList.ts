// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMutation, useQueryClient } from '@tanstack/react-query';

import type { CustomModelRemoved, CustomModelReport } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { modelMenuKeys } from './modelMenu';
import { requireClient } from './queries';

function useRefreshModels() {
    const queryClient = useQueryClient();
    return () => {
        void queryClient.invalidateQueries({ queryKey: ['providerModels'] });
        void queryClient.invalidateQueries({ queryKey: modelMenuKeys.active });
    };
}

/** Starts a model's download on the PC, as the Settings list does: `mutate(modelId)`. */
export function useStartModelDownload() {
    const client = useGaiaClient();
    const refresh = useRefreshModels();
    return useMutation<unknown, Error, string>({
        mutationFn: (model) => requireClient(client).downloadModel(model),
        onSettled: refresh,
    });
}

/** Checks a pasted Hugging Face link; nothing is saved. */
export function useCheckCustomModel() {
    const client = useGaiaClient();
    return useMutation<CustomModelReport, Error, { url: string; quant?: string }>({
        mutationFn: ({ url, quant }) => requireClient(client).checkCustomModel(url, quant),
    });
}

/** Adds the checked model to the list. */
export function useAddCustomModel() {
    const client = useGaiaClient();
    const refresh = useRefreshModels();
    return useMutation<{ report: CustomModelReport }, Error, { url: string; quant?: string }>({
        mutationFn: ({ url, quant }) => requireClient(client).addCustomModel(url, quant),
        onSuccess: refresh,
    });
}

/** Removes a model the person added, and its download. */
export function useRemoveCustomModel() {
    const client = useGaiaClient();
    const refresh = useRefreshModels();
    return useMutation<CustomModelRemoved, Error, string>({
        mutationFn: (model) => requireClient(client).removeCustomModel(model),
        onSuccess: refresh,
    });
}
