// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect } from 'vitest';
import type { AgentInfo } from '../../types';
import {
    packageType,
    groupIntoLanes,
    filterCatalog,
    trustTier,
    trustGateFor,
} from '../hubLanes';

function agent(partial: Partial<AgentInfo> & { id: string }): AgentInfo {
    return {
        name: partial.id,
        description: `${partial.id} description`,
        source: 'installed',
        conversation_starters: [],
        models: [],
        ...partial,
    };
}

describe('packageType', () => {
    it('defaults to agent when type is missing or unknown', () => {
        expect(packageType(agent({ id: 'a' }))).toBe('agent');
        // Unknown value falls back to agent.
        expect(packageType(agent({ id: 'a', type: 'weird' as never }))).toBe('agent');
    });

    it('passes app and component through', () => {
        expect(packageType(agent({ id: 'a', type: 'app' }))).toBe('app');
        expect(packageType(agent({ id: 'a', type: 'component' }))).toBe('component');
    });
});

describe('groupIntoLanes', () => {
    it('segments the catalog into app / component / agent lanes', () => {
        const lanes = groupIntoLanes([
            agent({ id: 'studio', type: 'app' }),
            agent({ id: 'rag-kit', type: 'component' }),
            agent({ id: 'chat', type: 'agent' }),
            agent({ id: 'legacy' }), // no type → agent
        ]);
        expect(lanes.app.map((a) => a.id)).toEqual(['studio']);
        expect(lanes.component.map((a) => a.id)).toEqual(['rag-kit']);
        expect(lanes.agent.map((a) => a.id)).toEqual(['chat', 'legacy']);
    });
});

describe('filterCatalog', () => {
    const catalog = [
        agent({ id: 'weather', name: 'Weather', description: 'forecast', category: 'utility' }),
        agent({ id: 'mars', name: 'Mars', description: 'space', tags: ['nasa'] }),
    ];

    it('returns everything for an empty query', () => {
        expect(filterCatalog(catalog, '  ')).toHaveLength(2);
    });

    it('matches name, description, category, and tags case-insensitively', () => {
        expect(filterCatalog(catalog, 'WEATHER').map((a) => a.id)).toEqual(['weather']);
        expect(filterCatalog(catalog, 'space').map((a) => a.id)).toEqual(['mars']);
        expect(filterCatalog(catalog, 'utility').map((a) => a.id)).toEqual(['weather']);
        expect(filterCatalog(catalog, 'nasa').map((a) => a.id)).toEqual(['mars']);
    });
});

describe('trustTier', () => {
    it('normalizes unknown/missing tiers to experimental', () => {
        expect(trustTier(agent({ id: 'a' }))).toBe('experimental');
        expect(trustTier(agent({ id: 'a', security_tier: 'verified' }))).toBe('verified');
        expect(trustTier(agent({ id: 'a', security_tier: 'community' }))).toBe('community');
    });
});

describe('trustGateFor — the verified-only refusal (issue #1722)', () => {
    it('does NOT require an override for a verified, sandboxed agent', () => {
        const gate = trustGateFor(agent({ id: 'safe', security_tier: 'verified' }));
        expect(gate.requiresOverride).toBe(false);
        expect(gate.trustNative).toBe(false);
        expect(gate.reasons).toHaveLength(0);
    });

    it('requires an override for a community agent (not AMD-verified)', () => {
        const gate = trustGateFor(agent({ id: 'comm', security_tier: 'community' }));
        expect(gate.requiresOverride).toBe(true);
        expect(gate.reasons.join(' ')).toMatch(/not audited/i);
    });

    it('requires an override for an experimental agent', () => {
        const gate = trustGateFor(agent({ id: 'exp', security_tier: 'experimental' }));
        expect(gate.requiresOverride).toBe(true);
        expect(gate.reasons.join(' ')).toMatch(/experimental/i);
    });

    it('flags a non-verified native agent as trustNative and gated', () => {
        const gate = trustGateFor(
            agent({ id: 'native', language: 'cpp', security_tier: 'experimental' }),
        );
        expect(gate.trustNative).toBe(true);
        expect(gate.requiresOverride).toBe(true);
        expect(gate.reasons.join(' ')).toMatch(/without sandboxing/i);
    });

    it('gates a deprecated agent even when verified', () => {
        const gate = trustGateFor(
            agent({ id: 'old', security_tier: 'verified', deprecated: true }),
        );
        expect(gate.requiresOverride).toBe(true);
        expect(gate.reasons.join(' ')).toMatch(/deprecated/i);
    });

    it('surfaces permissions and platform requirements', () => {
        const gate = trustGateFor(
            agent({
                id: 'p',
                security_tier: 'verified',
                permissions: ['fs:read', 'net:fetch'],
                requirements: { platforms: ['windows', 'linux'] },
            }),
        );
        expect(gate.permissions).toEqual(['fs:read', 'net:fetch']);
        expect(gate.platforms).toEqual(['windows', 'linux']);
    });
});
