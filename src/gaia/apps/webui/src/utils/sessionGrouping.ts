// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Group sidebar sessions by the agent that runs them (#2106).
 *
 * The session's ``agent_type`` is the source of truth (set at creation and by
 * the per-session agent picker). This read-side helper resolves each agent id
 * to a display name + icon using the loaded agent list, so the sidebar can
 * render one section per agent with its sessions nested underneath.
 */

import type { Session, AgentInfo } from '../types';

/** Sessions that predate explicit agent selection fall back to the chat agent,
 *  which is the historical default (`agent_type = "chat"` in the backend). */
export const DEFAULT_AGENT_ID = 'chat';

export interface AgentSessionGroup {
    /** Resolved agent id (session.agent_type, or the default). */
    agentId: string;
    /** Human-readable agent name — falls back to the id when the agent isn't
     *  in the loaded list (e.g. an uninstalled agent still owns old sessions). */
    name: string;
    /** Lucide icon name from the agent metadata, when known. */
    icon?: string;
    /** True when the agent id couldn't be resolved to a known agent. */
    unknown: boolean;
    sessions: Session[];
}

function updatedAtMs(s: Session): number {
    const t = new Date(s.updated_at).getTime();
    return Number.isNaN(t) ? 0 : t;
}

/**
 * Bucket sessions by agent, most-recently-active agent first, and sort each
 * bucket's sessions newest-first. Pure function — no store access — so it is
 * cheap to unit test and safe to memoize in the component.
 */
export function groupSessionsByAgent(
    sessions: Session[],
    agents: AgentInfo[],
): AgentSessionGroup[] {
    const byId = new Map<string, AgentInfo>();
    for (const a of agents) byId.set(a.id, a);

    const buckets = new Map<string, Session[]>();
    for (const s of sessions) {
        const key = s.agent_type || DEFAULT_AGENT_ID;
        const list = buckets.get(key);
        if (list) list.push(s);
        else buckets.set(key, [s]);
    }

    const groups: AgentSessionGroup[] = [];
    for (const [agentId, groupSessions] of buckets) {
        const agent = byId.get(agentId);
        groupSessions.sort((a, b) => updatedAtMs(b) - updatedAtMs(a));
        groups.push({
            agentId,
            name: agent?.name || agentId,
            icon: agent?.icon,
            unknown: agent === undefined,
            sessions: groupSessions,
        });
    }

    // Order groups by their most-recent session so the agent you last used
    // floats to the top — matching the recency the flat list used to have.
    groups.sort((a, b) => updatedAtMs(b.sessions[0]) - updatedAtMs(a.sessions[0]));
    return groups;
}

/** Resolve a session's agent display name (used for the flat/search list chip). */
export function resolveAgentName(session: Session, agents: AgentInfo[]): string {
    const id = session.agent_type || DEFAULT_AGENT_ID;
    return agents.find((a) => a.id === id)?.name || id;
}

/** Resolve a session's agent icon name (used for the flat/search list chip). */
export function resolveAgentIcon(session: Session, agents: AgentInfo[]): string | undefined {
    const id = session.agent_type || DEFAULT_AGENT_ID;
    return agents.find((a) => a.id === id)?.icon;
}
