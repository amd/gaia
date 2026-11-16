// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useQuery } from '@tanstack/react-query';

import type { DiscordStatus } from '@/api/client.types';
import { DISCORD_LIVE_STATES, DISCORD_POLL_MS } from '@/api/constants';
import { GaiaApiError } from '@/api/errors';

import { useGaiaClient } from './GaiaClientProvider';
import { requireClient } from './queries';

export const messagingKeys = {
    discord: ['messaging', 'discord'] as const,
};

/** The PC's Discord bridge, re-read while it can change by itself. A 403 (the PC answers only itself) is final, never retried. */
export function useDiscordStatus(enabled = true) {
    const client = useGaiaClient();
    return useQuery<DiscordStatus>({
        queryKey: messagingKeys.discord,
        enabled: client !== null && enabled,
        queryFn: ({ signal }) => requireClient(client).getDiscordStatus({ signal }),
        retry: (failures, error) => !(error instanceof GaiaApiError && error.status === 403) && failures < 3,
        refetchInterval: (query) => (query.state.data && DISCORD_LIVE_STATES.has(query.state.data.state) ? DISCORD_POLL_MS : false),
    });
}
