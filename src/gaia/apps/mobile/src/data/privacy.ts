// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { Settings, UndoSettings } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { modelMenuKeys } from './modelMenu';
import { modelKeys } from './models';
import { queryKeys, requireClient } from './queries';

export const privacyKeys = {
    settings: ['settings'] as const,
    undo: ['settings', 'undo'] as const,
};

/** The PC's settings; Privacy reads `network_policy` (Local only) from them. */
export function useSettings() {
    const client = useGaiaClient();
    return useQuery<Settings>({
        queryKey: privacyKeys.settings,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getSettings({ signal }),
    });
}

/** Turns Local only on or off: `mutate('local_ai_only' | 'cloud_allowed')`. The model chip and the provider lists re-read, since the PC clears cloud choices. */
export function useSetNetworkPolicy() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<Settings, Error, Settings['network_policy']>({
        mutationFn: (policy) => requireClient(client).setNetworkPolicy(policy),
        onSuccess: (saved) => queryClient.setQueryData(privacyKeys.settings, saved),
        onSettled: () => {
            void queryClient.invalidateQueries({ queryKey: modelMenuKeys.active });
            void queryClient.invalidateQueries({ queryKey: ['providerModels'] });
            // The key of `useCloudProviders` in setup.ts, which integration has and this branch's base lacks.
            void queryClient.invalidateQueries({ queryKey: ['cloudProviders'] });
            void queryClient.invalidateQueries({ queryKey: modelKeys.local });
            void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
        },
    });
}

export function useUndoSettings() {
    const client = useGaiaClient();
    return useQuery<UndoSettings>({
        queryKey: privacyKeys.undo,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getUndoSettings({ signal }),
    });
}

/** `mutate({ retentionDays, maxMb })`; the PC trims old copies before it answers. */
export function useUpdateUndoSettings() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<UndoSettings, Error, { retentionDays: number; maxMb: number }>({
        mutationFn: ({ retentionDays, maxMb }) => requireClient(client).updateUndoSettings(retentionDays, maxMb),
        onSuccess: (saved) => queryClient.setQueryData(privacyKeys.undo, saved),
    });
}

export interface DeleteAllResult {
    deleted: number;
    failed: { title: string; error: unknown }[];
}

/** Deletes each chat in turn, so one failure doesn't stop the rest: `mutate(chats)` answers with what failed. */
export function useDeleteAllChats() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<DeleteAllResult, Error, { id: string; title: string }[]>({
        mutationFn: async (chats) => {
            const c = requireClient(client);
            const result: DeleteAllResult = { deleted: 0, failed: [] };
            for (const chat of chats) {
                try {
                    await c.deleteSession(chat.id);
                    result.deleted += 1;
                    queryClient.removeQueries({ queryKey: queryKeys.session(chat.id) });
                    queryClient.removeQueries({ queryKey: queryKeys.messages(chat.id) });
                } catch (error) {
                    result.failed.push({ title: chat.title, error });
                }
            }
            return result;
        },
        onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.sessions }),
    });
}
