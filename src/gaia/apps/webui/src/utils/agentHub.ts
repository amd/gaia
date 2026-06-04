// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Pure helpers for the Agent Hub Installed/Available tabs and install flow
 * (issue #1097). Kept side-effect-free so the tab/card logic is unit-testable
 * without rendering React.
 */

import type { AgentInfo, InstallStatus } from '../types';

/** Format a byte count as a compact human-readable size (e.g. "1.4 GB"). */
export function formatBytes(bytes?: number | null): string {
    if (bytes == null || !Number.isFinite(bytes) || bytes <= 0) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit++;
    }
    // No decimals for bytes/KB; one decimal for MB and up.
    const digits = unit >= 2 ? 1 : 0;
    return `${value.toFixed(digits)} ${units[unit]}`;
}

/** True when an install-status snapshot represents an in-flight install. */
export function isInstalling(status?: InstallStatus | null): boolean {
    if (!status) return false;
    return status.state === 'downloading'
        || status.state === 'verifying'
        || status.state === 'installing';
}

/**
 * Compatibility level for the indicator dot. Falls back to ``compatible`` when
 * the catalog didn't supply a verdict (local-only agents are always runnable).
 */
export function compatLevel(
    agent: AgentInfo,
): 'compatible' | 'warning' | 'incompatible' {
    return agent.compatibility?.level ?? 'compatible';
}

/** Human label for a compatibility level. */
export function compatLabel(level: 'compatible' | 'warning' | 'incompatible'): string {
    switch (level) {
        case 'compatible': return 'Compatible with your system';
        case 'warning': return 'May run with limitations';
        case 'incompatible': return 'Not compatible with your system';
    }
}

/**
 * Merge catalog entries into the locally-registered agent list so installed
 * cards can show versions and update badges. Matches by id; when the catalog
 * marks an agent ``update_available`` (or reports a newer ``latest_version``),
 * the merged entry carries that status forward.
 */
export function mergeCatalogStatus(
    installed: AgentInfo[],
    catalog: AgentInfo[],
): AgentInfo[] {
    const byId = new Map(catalog.map((a) => [a.id, a]));
    return installed.map((agent) => {
        const cat = byId.get(agent.id);
        if (!cat) return agent;
        const hasUpdate =
            cat.status === 'update_available' ||
            (!!cat.latest_version && !!cat.version && cat.latest_version !== cat.version);
        return {
            ...agent,
            version: cat.version ?? agent.version,
            latest_version: cat.latest_version,
            compatibility: cat.compatibility ?? agent.compatibility,
            security_tier: cat.security_tier ?? agent.security_tier,
            deprecated: cat.deprecated ?? agent.deprecated,
            status: hasUpdate ? 'update_available' : 'installed',
        };
    });
}

/**
 * Agents shown on the Available tab: installable catalog entries not already
 * present locally. Installed agents — including those with a pending update —
 * are excluded here; updates are surfaced in place on the Installed tab.
 */
export function splitAvailable(
    catalog: AgentInfo[],
    installedIds: Set<string>,
): AgentInfo[] {
    return catalog.filter((a) => a.status === 'available' && !installedIds.has(a.id));
}

/** Count installed agents that have a pending update (for the tab label). */
export function countUpdates(agents: AgentInfo[]): number {
    return agents.filter((a) => a.status === 'update_available').length;
}

/**
 * Tab label with count and optional update suffix, e.g.
 * ``Installed (3) · 1 update`` / ``Installed (3) · 2 updates``.
 */
export function installedTabLabel(count: number, updates: number): string {
    const base = `Installed (${count})`;
    if (updates <= 0) return base;
    return `${base} · ${updates} ${updates === 1 ? 'update' : 'updates'}`;
}
