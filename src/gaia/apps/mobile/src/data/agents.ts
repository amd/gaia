// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';

import { useQuery, useQueryClient } from '@tanstack/react-query';

import { FLAGSHIP_AGENT_ID } from '@webui/utils/newTask';

import type { AgentInstallStatus, GaiaClient, SetupCheck } from '@/api/client.types';
import { GaiaApiError } from '@/api/errors';

import { useGaiaClient } from './GaiaClientProvider';
import { useAgents } from './connectors';
import { queryKeys, requireClient } from './queries';

export const INSTALL_POLL_MS = 1_500;

const setupCheckKey = ['setupCheck'] as const;

const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Installs the GAIA agent on the PC and waits for it to finish. Resolves on `completed`, throws with the
 * PC's reason on `failed` or a refused start. An install the PC is already running (409) is waited on;
 * any other 409 is reported.
 */
export async function installFlagshipAgent(
    client: GaiaClient,
    {
        onProgress,
        isCancelled = () => false,
        pollMs = INSTALL_POLL_MS,
    }: { onProgress?: (status: AgentInstallStatus) => void; isCancelled?: () => boolean; pollMs?: number } = {},
): Promise<void> {
    try {
        await client.installAgent(FLAGSHIP_AGENT_ID);
    } catch (err) {
        if (!(err instanceof GaiaApiError) || err.status !== 409) throw err;
        // A 409 also means "core too old" or "another process is installing"; wait only on an install the PC tracks.
        const first = await client.getAgentInstallStatus(FLAGSHIP_AGENT_ID).catch(() => null);
        if (!first || first.status === 'completed' || first.status === 'failed') throw err;
    }
    while (!isCancelled()) {
        const status = await client.getAgentInstallStatus(FLAGSHIP_AGENT_ID);
        onProgress?.(status);
        if (status.status === 'completed') return;
        if (status.status === 'failed') throw new Error(status.error || 'The GAIA agent could not be installed.');
        await delay(pollMs);
    }
}

export type FlagshipInstallState =
    | { kind: 'idle' }
    | { kind: 'installing'; percent: number }
    | { kind: 'failed'; error: unknown };

/**
 * Whether the PC lacks the GAIA agent, so every new chat would be refused. `noBuild` says the hub has no
 * agent build for this PC (nothing to install); it is read only while the agent is missing.
 * `install()` starts the install on the PC and reports `state` until the agent list shows it.
 */
export function useFlagshipAgent() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const agents = useAgents();
    const missing = !!agents.data && !agents.data.agents.some((a) => a.id === FLAGSHIP_AGENT_ID);
    const setup = useQuery<SetupCheck>({
        queryKey: setupCheckKey,
        enabled: client !== null && missing,
        queryFn: ({ signal }) => requireClient(client).checkSetup(false, { signal }),
    });
    const [state, setState] = useState<FlagshipInstallState>({ kind: 'idle' });
    const cancelled = useRef(false);
    useEffect(() => {
        cancelled.current = false;
        return () => {
            cancelled.current = true;
        };
    }, []);

    const install = useCallback(async () => {
        const live = requireClient(client);
        setState({ kind: 'installing', percent: 0 });
        try {
            await installFlagshipAgent(live, {
                isCancelled: () => cancelled.current,
                onProgress: (s) => {
                    if (!cancelled.current) setState({ kind: 'installing', percent: s.percent ?? 0 });
                },
            });
            if (cancelled.current) return;
            await queryClient.invalidateQueries({ queryKey: queryKeys.agents });
            if (!cancelled.current) setState({ kind: 'idle' });
        } catch (error) {
            if (!cancelled.current) setState({ kind: 'failed', error });
        }
    }, [client, queryClient]);

    return { missing, noBuild: missing && !!setup.data?.no_agent_build, state, install };
}
