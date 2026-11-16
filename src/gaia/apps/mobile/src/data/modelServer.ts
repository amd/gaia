// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { LemonadeTarget, LemonadeTargetBody, MCPServerStatus, Settings } from '@/api/client.types';
import type { ModelServerInfo } from '@webui/types/api.gen';

import { useGaiaClient } from './GaiaClientProvider';
import { privacyKeys } from './privacy';
import { queryKeys, requireClient } from './queries';

export const modelServerKeys = {
    info: ['modelServer'] as const,
    mcp: ['mcpStatus'] as const,
    target: ['lemonadeTarget'] as const,
};

/** Which model server GAIA uses, its address and what each loaded model runs on. */
export function useModelServer() {
    const client = useGaiaClient();
    return useQuery<ModelServerInfo>({
        queryKey: modelServerKeys.info,
        enabled: client !== null,
        retry: false,
        queryFn: ({ signal }) => requireClient(client).getModelServer({ signal }),
    });
}

/** Saves the llama.cpp backend for chat models: `mutate('vulkan')`. */
export function useSetModelServerBackend() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<ModelServerInfo, Error, string>({
        mutationFn: (backend) => requireClient(client).setModelServerBackend(backend),
        onSuccess: (saved) => queryClient.setQueryData(modelServerKeys.info, saved),
    });
}

/** Switches GAIA to its own model server or one the PC runs; the status and model lists re-read. */
export function useSetLemonadeTarget() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<LemonadeTarget, Error, LemonadeTargetBody>({
        mutationFn: (body) => requireClient(client).setLemonadeTarget(body),
        onSuccess: (saved) => queryClient.setQueryData(modelServerKeys.target, saved),
        onSettled: () => {
            void queryClient.invalidateQueries({ queryKey: modelServerKeys.info });
            void queryClient.invalidateQueries({ queryKey: queryKeys.systemStatus });
        },
    });
}

/** Saves the custom model ID or the dynamic-tools switch. */
export function useUpdateSettings() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<Settings, Error, { custom_model?: string; dynamic_tools?: boolean }>({
        mutationFn: (patch) => requireClient(client).updateSettings(patch),
        onSuccess: (saved) => queryClient.setQueryData(privacyKeys.settings, saved),
        onSettled: () => {
            void queryClient.invalidateQueries({ queryKey: queryKeys.systemStatus });
        },
    });
}

/** The MCP servers GAIA runs and whether each connected. */
export function useMcpStatus() {
    const client = useGaiaClient();
    return useQuery<{ servers: MCPServerStatus[] }>({
        queryKey: modelServerKeys.mcp,
        enabled: client !== null,
        retry: false,
        queryFn: ({ signal }) => requireClient(client).getMcpStatus({ signal }),
    });
}
