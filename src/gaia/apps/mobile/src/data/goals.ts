// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useQuery } from '@tanstack/react-query';

import type { Goal } from '@/api/client.types';
import { GOALS_POLL_MS } from '@/api/constants';

import { useGaiaClient } from './GaiaClientProvider';
import { failedWithoutData, requireClient } from './queries';

export const goalKeys = {
    list: ['goals'] as const,
};

/** Every goal with its tasks, read-only; re-read while the screen is open so task states follow the PC. */
export function useGoals() {
    const client = useGaiaClient();
    return useQuery<Goal[]>({
        queryKey: goalKeys.list,
        enabled: client !== null,
        queryFn: async ({ signal }) => (await requireClient(client).listGoals({ signal })).goals,
        refetchInterval: (query) => (failedWithoutData(query) ? false : GOALS_POLL_MS),
    });
}
