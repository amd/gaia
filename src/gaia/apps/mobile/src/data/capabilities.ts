// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';

import { useQuery, useQueryClient } from '@tanstack/react-query';

import type { AgentCapabilities } from '@/api/client.types';
import { CAPABILITIES_POLL_MS } from '@/api/constants';

import { useGaiaClient } from './GaiaClientProvider';
import { queryKeys, requireClient } from './queries';

/**
 * The tools and skills of the chat's running agent. Reads only while `enabled` (a section is open);
 * while `live` (a reply runs or waits on a prompt) it re-reads every 2 s and once more when the reply ends.
 * A 409 means the agent hasn't started yet; the query then errors with it.
 */
export function useCapabilities(sessionId: string, { enabled, live }: { enabled: boolean; live: boolean }) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const wasLive = useRef(live);
    useEffect(() => {
        if (wasLive.current && !live && enabled)
            void queryClient.invalidateQueries({
                queryKey: queryKeys.capabilities(sessionId),
            });
        wasLive.current = live;
    }, [live, enabled, queryClient, sessionId]);
    return useQuery<AgentCapabilities>({
        queryKey: queryKeys.capabilities(sessionId),
        enabled: enabled && client !== null,
        queryFn: ({ signal }) => requireClient(client).getCapabilities(sessionId, { signal }),
        refetchInterval: live ? CAPABILITIES_POLL_MS : false,
        retry: false,
    });
}
