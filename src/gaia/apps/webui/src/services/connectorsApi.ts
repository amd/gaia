// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Connector calls, re-exported by api.ts; callers keep importing from there. */

import type { AgentMcpServer, ConnectorRow } from '../types';
import { apiFetch, UI_HEADER } from './api';

// -- Connections (issue #915) ---------------------------------------------------

// New framework endpoints (T-8b) — /api/connectors

export async function listConnectors(): Promise<{ connectors: ConnectorRow[] }> {
    return apiFetch('GET', '/connectors');
}

export async function getConnector(connectorId: string): Promise<ConnectorRow> {
    return apiFetch('GET', `/connectors/${connectorId}`);
}

export async function authorizeConnector(
    connectorId: string,
    scopes: string[],
    grantAgents: string[] = [],
): Promise<{ flow_id: string; authorization_url: string }> {
    // `grantAgents` (#2117) — namespaced agent ids to grant this connector the
    // moment OAuth completes, so connecting a mailbox grants it to the email
    // agent in the same flow. The backend resolves each agent's required scopes.
    return apiFetch(
        'POST',
        `/connectors/${connectorId}/authorize`,
        { scopes, grant_agents: grantAgents },
        UI_HEADER,
    );
}

export interface DeviceAuthInfo {
    user_code: string;
    verification_uri: string;
    expires_in: number;
    interval: number;
    message: string;
}

export async function authorizeConnectorDevice(
    connectorId: string,
    scopes: string[],
    grantAgents: string[] = [],
): Promise<DeviceAuthInfo> {
    // Device-code sign-in: no browser redirect / no Azure app registration.
    // The server polls in the background and emits connector.oauth.completed /
    // connector.oauth.error over SSE (useConnectorsSSE) when the user finishes,
    // so the caller displays the code and waits for the same events the browser
    // flow uses. `grantAgents` are committed atomically on success.
    return apiFetch(
        'POST',
        `/connectors/${connectorId}/authorize-device`,
        { scopes, grant_agents: grantAgents },
        UI_HEADER,
    );
}

export async function configureConnector(
    connectorId: string,
    config: Record<string, unknown>,
): Promise<Record<string, unknown>> {
    return apiFetch('POST', `/connectors/${connectorId}/configure`, { config }, UI_HEADER);
}

export async function disconnectConnector(connectorId: string): Promise<void> {
    await apiFetch<unknown>('DELETE', `/connectors/${connectorId}`, undefined, UI_HEADER);
}

/**
 * Enable a previously-disabled MCP connector (#1004).
 *
 * Returns the updated ConnectorRow with `enabled: true`. The backend
 * triggers a live reload of every cached chat session's MCPClientManager
 * so the connector's tools materialize without restarting GAIA.
 *
 * Throws when called against a non-MCP connector (server returns 400).
 */
export async function enableConnector(connectorId: string): Promise<ConnectorRow> {
    return apiFetch('POST', `/connectors/${connectorId}/enable`, {}, UI_HEADER);
}

/**
 * Disable a configured MCP connector without clearing credentials (#1004).
 *
 * Credentials, the env-block ``$keyring`` references, and per-agent grants
 * are preserved. The backend triggers a live reload so the connector's
 * tools disappear from active agents' tool lists. Re-enable with
 * ``enableConnector`` to make tools available again.
 *
 * Throws when called against a non-MCP connector (server returns 400).
 */
export async function disableConnector(connectorId: string): Promise<ConnectorRow> {
    return apiFetch('POST', `/connectors/${connectorId}/disable`, {}, UI_HEADER);
}

/**
 * List MCP servers declared by custom Python agents (#1020).
 *
 * Returns a flat sorted list (enabled first, alphabetical) of server entries
 * from each custom agent's local mcp_servers.json. These are read-only — the
 * UI renders them without a toggle or disconnect action.
 */
export async function listAgentMcps(): Promise<{ agent_mcps: AgentMcpServer[] }> {
    return apiFetch('GET', '/connectors/agent-mcps');
}

export async function listConnectorGrants(connectorId: string): Promise<{
    grants: Record<string, string[]>;
}> {
    return apiFetch('GET', `/connectors/${connectorId}/grants`);
}

export async function grantConnectorAgent(
    connectorId: string,
    agentId: string,
    scopes: string[],
): Promise<void> {
    await apiFetch<unknown>(
        'PUT',
        `/connectors/${connectorId}/grants/${encodeURIComponent(agentId)}`,
        { scopes },
        UI_HEADER,
    );
}

export async function revokeConnectorAgentGrant(
    connectorId: string,
    agentId: string,
): Promise<void> {
    await apiFetch<unknown>(
        'DELETE',
        `/connectors/${connectorId}/grants/${encodeURIComponent(agentId)}`,
        undefined,
        UI_HEADER,
    );
}

/**
 * List per-agent activations for a connector (issue #1005).
 *
 * Activations gate MCP tool visibility — an agent must be both granted
 * (credential access) AND activated (tool visibility) for an MCP server's
 * tools to appear in its system prompt. This endpoint is type-agnostic for
 * read convenience: OAuth connectors return ``{}`` because the mutating
 * routes refuse to write to them.
 */
export async function listConnectorActivations(connectorId: string): Promise<{
    activations: Record<string, boolean>;
}> {
    return apiFetch('GET', `/connectors/${connectorId}/activations`);
}

interface ActivateConnectorResponse {
    connector_id: string;
    agent_id: string;
    active: boolean;
    auto_granted: boolean;
}

/**
 * Activate an MCP-server connector for an agent. If no grant exists yet,
 * ``scopes`` is used to auto-create one (one-click convenience). Without
 * ``scopes`` the request returns 400 when no prior grant exists.
 *
 * Only valid for ``mcp_server`` connectors — calling this for an OAuth
 * provider returns ``400 Bad Request``. OAuth per-agent access is
 * controlled by the grants endpoints above. The Agent UI hides the
 * "Active for" section for OAuth tiles to surface this at the UI layer.
 */
export async function activateConnectorAgent(
    connectorId: string,
    agentId: string,
    scopes?: string[],
): Promise<ActivateConnectorResponse> {
    return apiFetch<ActivateConnectorResponse>(
        'PUT',
        `/connectors/${connectorId}/activations/${encodeURIComponent(agentId)}`,
        scopes ? { scopes } : {},
        UI_HEADER,
    );
}

/**
 * Deactivate an MCP-server connector for an agent. Non-destructive — the
 * grant survives so a later re-activate is one click. Only valid for
 * ``mcp_server`` connectors (see :func:`activateConnectorAgent`).
 */
export async function deactivateConnectorAgent(
    connectorId: string,
    agentId: string,
): Promise<void> {
    await apiFetch<unknown>(
        'DELETE',
        `/connectors/${connectorId}/activations/${encodeURIComponent(agentId)}`,
        undefined,
        UI_HEADER,
    );
}
