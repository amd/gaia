// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, describe, expect, it, vi } from 'vitest';

import { installCommandFor, skillsFromIndex, toHubSkill, type HubCatalogEntry } from './hub';

// Shaped like the Worker's toSkillIndexEntry output, trimmed to what the page reads.
function skill(id: string, overrides: Partial<HubCatalogEntry> = {}): HubCatalogEntry {
  return {
    id,
    name: id,
    description: `The ${id} skill.`,
    type: 'skill',
    latest_version: '1.2.0',
    security_tier: 'verified',
    skill_metadata: {},
    ...overrides,
  };
}

describe('skillsFromIndex', () => {
  it('keeps only type=skill entries, in catalog order', () => {
    const index = {
      schema_version: 1,
      agents: [
        { id: 'agent-ui', type: 'app', latest_version: '0.25.0', security_tier: 'experimental' },
        skill('summarize'),
        { id: 'gaia', type: 'agent', latest_version: '0.3.2', security_tier: 'verified' },
        { id: 'terminal-hub', type: 'component', latest_version: '0.1.0', security_tier: 'verified' },
        // No type is an agent: the lane defaulted to agent before the discriminator existed.
        { id: 'legacy', latest_version: '0.1.0', security_tier: 'verified' },
        skill('coding'),
      ],
    };
    expect(skillsFromIndex(index).map((s) => s.id)).toEqual(['summarize', 'coding']);
  });

  it('returns an empty list for a catalog with no skills', () => {
    const index = {
      agents: [{ id: 'gaia', type: 'agent', latest_version: '0.3.2', security_tier: 'verified' }],
    };
    expect(skillsFromIndex(index)).toEqual([]);
  });

  it('returns an empty list for an empty catalog', () => {
    expect(skillsFromIndex({ schema_version: 1, agents: [] })).toEqual([]);
  });

  it('throws when the agents array is missing', () => {
    expect(() => skillsFromIndex({ schema_version: 1 })).toThrow(/\[hub\].*'agents' array/);
    expect(() => skillsFromIndex(null)).toThrow(/\[hub\]/);
  });
});

describe('toHubSkill', () => {
  it('maps the card fields and the source link', () => {
    expect(toHubSkill(skill('summarize'))).toEqual({
      id: 'summarize',
      name: 'summarize',
      description: 'The summarize skill.',
      version: '1.2.0',
      tier: 'verified',
      installCommand: 'gaia skill install summarize',
      sourceUrl: 'https://github.com/amd/gaia/tree/main/hub/skills/summarize',
    });
  });

  it('prefers latest_version and falls back to version', () => {
    expect(toHubSkill(skill('a', { latest_version: '2.0.0', version: '1.0.0' })).version).toBe('2.0.0');
    expect(toHubSkill(skill('a', { latest_version: undefined, version: '1.0.0' })).version).toBe('1.0.0');
  });

  it('reads the tier from the top level or from skill_metadata', () => {
    expect(
      toHubSkill(skill('a', { security_tier: 'community', skill_metadata: { security_tier: 'verified' } })).tier,
    ).toBe('community');
    expect(
      toHubSkill(skill('a', { security_tier: undefined, skill_metadata: { security_tier: 'community' } })).tier,
    ).toBe('community');
  });

  it('throws naming the skill when the version is missing', () => {
    expect(() => toHubSkill(skill('a', { latest_version: undefined }))).toThrow(
      /\[hub\] Skill 'a'.*'latest_version'/,
    );
  });

  it('throws naming the skill when the tier is missing', () => {
    expect(() => toHubSkill(skill('a', { security_tier: undefined }))).toThrow(
      /\[hub\] Skill 'a'.*'security_tier'/,
    );
  });

  it('throws when the id is missing', () => {
    expect(() => toHubSkill(skill('a', { id: undefined }))).toThrow(/\[hub\].*no 'id'/);
  });
});

describe('installCommandFor', () => {
  it('is the plain install for verified and community skills', () => {
    expect(installCommandFor('coding', 'verified')).toBe('gaia skill install coding');
    expect(installCommandFor('coding', 'community')).toBe('gaia skill install coding');
  });

  it('adds the opt-in flag the CLI requires for experimental skills', () => {
    expect(installCommandFor('coding', 'experimental')).toBe(
      'gaia skill install coding --allow-experimental',
    );
  });
});

describe('getHubSkills — error handling', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('returns the skills lane of the fetched catalog', async () => {
    vi.resetModules();
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ schema_version: 1, agents: [skill('summarize')] }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const mod = await import('./hub');
    expect((await mod.getHubSkills()).map((s) => s.id)).toEqual(['summarize']);
    expect(fetchMock).toHaveBeenCalledWith('https://hub.amd-gaia.ai/index.json');
  });

  it('throws a [hub]-prefixed error on a non-200 response', async () => {
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn(async () => new Response('down', { status: 503 })));

    const mod = await import('./hub');
    await expect(mod.getHubSkills()).rejects.toThrow(/\[hub\].*index\.json.*HTTP 503/);
  });

  it('throws a [hub]-prefixed error when the hub is unreachable', async () => {
    vi.resetModules();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed');
      }),
    );

    const mod = await import('./hub');
    await expect(mod.getHubSkills()).rejects.toThrow(/\[hub\] Failed to fetch.*fetch failed/);
  });
});
