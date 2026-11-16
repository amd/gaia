// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { buildPersonalizePrompt, personalizeSourceKind } from '@webui/utils/connectorPersonalize';

import type { AgentInfo, ConnectorRow } from '@/api/client.types';

/** Canonical scope for MCP-server grants; one-click activation grants it when the agent has none. */
export const MCP_DEFAULT_GRANT_SCOPES = ['use'];

/** The agent a personalization chat runs on. */
export const FLAGSHIP_AGENT_ID = 'gaia';

export type AccessAgent = AgentInfo & { namespaced_agent_id: string };

function named(agents: readonly AgentInfo[]): AccessAgent[] {
    return agents.filter((a): a is AccessAgent => !!a.namespaced_agent_id);
}

function requires(agent: AgentInfo, connectorId: string) {
    return agent.required_connections?.find((rc) => rc.connector_id === connectorId);
}

/** Agents that declare a requirement on this connector: each gets one toggle per scope. */
export function grantAgents(agents: readonly AgentInfo[], connectorId: string): AccessAgent[] {
    return named(agents).filter((a) => requires(a, connectorId));
}

/** Agents that may be switched on for an MCP server: dynamic consumers and declared requirers. */
export function activatableAgents(agents: readonly AgentInfo[], connector: ConnectorRow): AccessAgent[] {
    if (connector.type !== 'mcp_server') return [];
    return named(agents).filter((a) => a.consumes_mcp_servers || requires(a, connector.id));
}

/** The one assistant that can use this MCP server when GAIA is the only one; it gets a single switch. */
export function soleMcpAgent(agents: readonly AgentInfo[], connector: ConnectorRow): AccessAgent | null {
    const usable = activatableAgents(agents, connector);
    const needsGrant = agents.some((a) => requires(a, connector.id));
    if (usable.length !== 1 || usable[0]!.id !== FLAGSHIP_AGENT_ID || needsGrant) return null;
    return usable[0]!;
}

/** The scopes to create a grant with when an agent is switched on and holds none. */
export function scopesForActivation(agent: AgentInfo, connectorId: string, granted: readonly string[]): string[] | undefined {
    if (granted.length > 0) return undefined;
    const req = requires(agent, connectorId);
    return req ? [...req.scopes] : [...MCP_DEFAULT_GRANT_SCOPES];
}

/** The scopes an agent holds after one scope switches. */
export function nextScopes(granted: readonly string[], scope: string, on: boolean): string[] {
    const rest = granted.filter((s) => s !== scope);
    return on ? [...rest, scope] : rest;
}

/** The new-chat route, with the personalization prompt waiting in the box. */
export function personalizeRoute(connector: ConnectorRow) {
    const prompt = buildPersonalizePrompt({ connectorId: connector.id, displayName: connector.display_name });
    return { pathname: '/new' as const, params: { prompt, promptId: `personalize:${connector.id}` } };
}

/** The flagship's grant key, or null while GAIA's main agent isn't listed. */
export function flagshipAgentId(agents: readonly AgentInfo[]): string | null {
    return agents.find((a) => a.id === FLAGSHIP_AGENT_ID)?.namespaced_agent_id ?? null;
}

export { personalizeSourceKind };
