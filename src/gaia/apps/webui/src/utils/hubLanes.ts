// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Pure helpers for the in-app Hub page (issue #1722): segment the merged
 * catalog into Apps · Components · Agents lanes, and compute the install trust
 * gate. Side-effect-free so the lane/gate logic is unit-testable without React.
 *
 * DATA SOURCE (dev/testing): the lanes are driven by ``AgentInfo.type``, which
 * the UI backend now propagates from the local registry merge
 * (``gaia.hub.catalog.merge_with_registry``). The production R2 catalog +
 * publish pipeline (#1717 schema, #1718 worker, #1719 R2 publish) is not live
 * yet; when it lands the ``type`` field arrives from ``index.json`` and this
 * helper is unchanged — it's a source swap, not a UI rewrite.
 */

import type { AgentInfo } from '../types';

export type PackageType = 'agent' | 'app' | 'component';

export interface LaneDef {
    key: PackageType;
    /** Plural lane heading shown to the user. */
    title: string;
    /** One-line lane subtitle. */
    subtitle: string;
}

/**
 * Lane render order: Apps first (most user-facing), then Components, then
 * Agents. Mirrors the issue's "Apps · Components · Agents" segmentation.
 */
export const LANES: readonly LaneDef[] = [
    { key: 'app', title: 'Apps', subtitle: 'Full experiences you can launch' },
    { key: 'component', title: 'Components', subtitle: 'Reusable building blocks' },
    { key: 'agent', title: 'Agents', subtitle: 'Task-focused AI agents' },
] as const;

/** Normalize a catalog entry's package kind, defaulting to ``agent`` (#1716). */
export function packageType(agent: AgentInfo): PackageType {
    return agent.type === 'app' || agent.type === 'component' ? agent.type : 'agent';
}

/** Grouped catalog: one array per lane, preserving input order within a lane. */
export type Lanes = Record<PackageType, AgentInfo[]>;

/** Split the catalog into per-type lanes. */
export function groupIntoLanes(catalog: AgentInfo[]): Lanes {
    const lanes: Lanes = { app: [], component: [], agent: [] };
    for (const a of catalog) {
        lanes[packageType(a)].push(a);
    }
    return lanes;
}

/**
 * Case-insensitive text filter over name / description / tags / category.
 * Empty query returns the list unchanged.
 */
export function filterCatalog(catalog: AgentInfo[], query: string): AgentInfo[] {
    const q = query.trim().toLowerCase();
    if (!q) return catalog;
    return catalog.filter(
        (a) =>
            a.name.toLowerCase().includes(q) ||
            a.description.toLowerCase().includes(q) ||
            (a.category ?? '').toLowerCase().includes(q) ||
            (a.tags ?? []).some((t) => t.toLowerCase().includes(q)),
    );
}

// ── Trust gate ─────────────────────────────────────────────────────────────

export type TrustTier = 'verified' | 'community' | 'experimental';

export interface TrustGate {
    /** Normalized security tier. */
    tier: TrustTier;
    /**
     * True when the user MUST explicitly override before install can proceed.
     * The issue's hard requirement: anything not ``verified`` is gated. Native
     * (unsandboxed C++) and deprecated agents are always gated too.
     */
    requiresOverride: boolean;
    /**
     * Whether installing sends ``trust_native`` to the backend. Mirrors
     * ``requiresOverride`` — any agent the user had to explicitly override
     * (non-verified, native, or deprecated) installs with the trust flag set,
     * not just native packages.
     */
    trustNative: boolean;
    /** Declared permission scopes, e.g. ``["fs:read", "net:fetch"]``. */
    permissions: string[];
    /** Platform requirements, e.g. ``["windows", "linux"]``. */
    platforms: string[];
    /** Human-readable reasons the gate is raised (empty when not gated). */
    reasons: string[];
}

/** Normalize a possibly-undefined tier to a known value (default experimental). */
export function trustTier(agent: AgentInfo): TrustTier {
    const t = agent.security_tier;
    return t === 'verified' || t === 'community' ? t : 'experimental';
}

/**
 * Compute the install trust gate for an agent.
 *
 * The gate refuses a one-click install and demands an explicit override when
 * the agent is not AMD-``verified`` (the issue's hard requirement), when it
 * ships an unsandboxed native binary, or when the publisher deprecated it.
 * Verified, sandboxed, non-deprecated agents install in one click.
 */
export function trustGateFor(agent: AgentInfo): TrustGate {
    const tier = trustTier(agent);
    // Native = ships an unsandboxed C++ binary. Deliberately NOT keyed off
    // agent.requires_trust — that field now covers ANY non-verified agent,
    // not just native ones, so folding it in here would mislabel a
    // non-verified python agent as "ships a native binary".
    const isNative = agent.language === 'cpp' && tier !== 'verified';
    const reasons: string[] = [];

    if (tier !== 'verified') {
        reasons.push(
            tier === 'community'
                ? 'Community-published — not audited by AMD.'
                : 'Experimental — unreviewed and may be unstable.',
        );
    }
    if (isNative) {
        reasons.push('Ships a native binary that runs on your machine without sandboxing.');
    }
    if (agent.deprecated) {
        reasons.push('Deprecated by the publisher — may be unmaintained or superseded.');
    }

    const requiresOverride = tier !== 'verified' || isNative || !!agent.deprecated;

    return {
        tier,
        requiresOverride,
        // Send the trust override whenever the user had to acknowledge one —
        // covers every non-verified agent, not just native packages.
        trustNative: requiresOverride,
        permissions: agent.permissions ?? [],
        platforms: agent.requirements?.platforms ?? [],
        reasons,
    };
}
