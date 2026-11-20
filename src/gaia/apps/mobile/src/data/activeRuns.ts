// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useQuery } from '@tanstack/react-query';

import type { ActiveRuns } from '@/api/client.types';
import { APP_POLL_MS } from '@/api/constants';

import { useGaiaClient } from './GaiaClientProvider';
import { failedWithoutData, requireClient } from './queries';

export const activeRunsKey = ['chat', 'active'] as const;

/** Which chats on the PC have a turn running, queued or waiting on the person; re-read while the app is open (paused in the background by the focus manager). */
export function useActiveRuns() {
    const client = useGaiaClient();
    return useQuery<ActiveRuns>({
        queryKey: activeRunsKey,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getActiveRuns({ signal }),
        refetchInterval: (query) => (failedWithoutData(query) ? false : APP_POLL_MS),
    });
}
