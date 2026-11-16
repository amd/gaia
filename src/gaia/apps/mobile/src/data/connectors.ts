// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { AgentList, AgentMcpList, ConnectorActivations, ConnectorGrants, ConnectorList, DisconnectOutcome, GaiaClient } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { queryKeys, requireClient } from './queries';

/** Every connector this PC knows, including those whose live state GAIA couldn't read. */
export function useConnectors() {
    const client = useGaiaClient();
    return useQuery<ConnectorList>({
        queryKey: queryKeys.connectors,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).listConnectors({ signal }),
    });
}

/** MCP servers that custom agents declare themselves (read-only). */
export function useAgentMcps() {
    const client = useGaiaClient();
    return useQuery<AgentMcpList>({
        queryKey: queryKeys.agentMcps,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).listAgentMcps({ signal }),
    });
}

/** The agents on this PC; each connector's grants name them by `namespaced_agent_id`. */
export function useAgents() {
    const client = useGaiaClient();
    return useQuery<AgentList>({
        queryKey: queryKeys.agents,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).listAgents({ signal }),
    });
}

/** Which scopes each agent holds on one connector; `enabled` waits for its tile to open. */
export function useConnectorGrants(connectorId: string, enabled: boolean) {
    const client = useGaiaClient();
    return useQuery<ConnectorGrants>({
        queryKey: queryKeys.connectorGrants(connectorId),
        enabled: enabled && client !== null,
        queryFn: ({ signal }) => requireClient(client).listConnectorGrants(connectorId, { signal }),
    });
}

/** Which agents may use one MCP server's tools. */
export function useConnectorActivations(connectorId: string, enabled: boolean) {
    const client = useGaiaClient();
    return useQuery<ConnectorActivations>({
        queryKey: queryKeys.connectorActivations(connectorId),
        enabled: enabled && client !== null,
        queryFn: ({ signal }) => requireClient(client).listConnectorActivations(connectorId, { signal }),
    });
}

export type ConnectorAccessChange =
    | { kind: 'grant'; agentId: string; scopes: string[] }
    | { kind: 'activate'; agentId: string; scopes?: string[] }
    | { kind: 'deactivate'; agentId: string };

function applyChange(client: GaiaClient, connectorId: string, change: ConnectorAccessChange): Promise<void> {
    switch (change.kind) {
        case 'grant':
            return change.scopes.length === 0
                ? client.revokeConnectorAgentGrant(connectorId, change.agentId)
                : client.grantConnectorAgent(connectorId, change.agentId, change.scopes);
        case 'activate':
            return client.activateConnectorAgent(connectorId, change.agentId, change.scopes);
        case 'deactivate':
            return client.deactivateConnectorAgent(connectorId, change.agentId);
    }
}

/** Changes what an agent may do with one connector, then reloads that connector's grants and activations. */
export function useChangeConnectorAccess(connectorId: string) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<void, Error, ConnectorAccessChange>({
        mutationFn: (change) => applyChange(requireClient(client), connectorId, change),
        onSettled: () =>
            Promise.all([
                queryClient.invalidateQueries({ queryKey: queryKeys.connectorGrants(connectorId) }),
                queryClient.invalidateQueries({ queryKey: queryKeys.connectorActivations(connectorId) }),
                queryClient.invalidateQueries({ queryKey: queryKeys.connectors }),
            ]),
    });
}

/** Pauses an MCP server, or removes its credentials (resolving with the revoke outcome), then reloads the connector list. */
export function useConnectorServerAction(connectorId: string, onDisconnected?: (outcome: DisconnectOutcome | null) => void) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<DisconnectOutcome | null | undefined, Error, 'disable' | 'disconnect'>({
        mutationFn: (action) => {
            const c = requireClient(client);
            return action === 'disable' ? c.disableConnector(connectorId).then(() => undefined) : c.disconnectConnector(connectorId);
        },
        // Runs even after the tile moved to another group and remounted.
        onSuccess: (outcome, action) => {
            if (action === 'disconnect') onDisconnected?.(outcome ?? null);
        },
        onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.connectors }),
    });
}
