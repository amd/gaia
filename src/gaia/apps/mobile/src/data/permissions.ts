// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { AllPermissions, PermissionMode } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { requireClient } from './queries';

/** Shares the `['permissions', …]` prefix with the composer's per-chat key, so one invalidation re-reads both. */
export const permissionsKeys = {
    all: ['permissions'] as const,
    overview: ['permissions', 'overview'] as const,
};

export function useAllPermissions() {
    const client = useGaiaClient();
    return useQuery<AllPermissions>({
        queryKey: permissionsKeys.overview,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).listAllPermissions({ signal }),
    });
}

/** The permission changes Settings makes; each re-reads every permission query once it settles. */
export function usePermissionActions() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const settled = () => queryClient.invalidateQueries({ queryKey: permissionsKeys.all });
    const setDefault = useMutation<unknown, Error, PermissionMode>({
        mutationFn: (mode) => requireClient(client).setDefaultPermissionMode(mode),
        onSettled: settled,
    });
    const setChatMode = useMutation<unknown, Error, { sessionId: string; mode: PermissionMode }>({
        mutationFn: ({ sessionId, mode }) => requireClient(client).setPermissionMode(sessionId, mode),
        onSettled: settled,
    });
    const revoke = useMutation<unknown, Error, { sessionId: string; key?: string }>({
        mutationFn: ({ sessionId, key }) => requireClient(client).revokeGrants(sessionId, key),
        onSettled: settled,
    });
    return { setDefault, setChatMode, revoke };
}
