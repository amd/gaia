// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Pure catalog helpers — the lane discriminator and the per-lane install
// command. No network: loadCatalog() is not exercised here.

import { describe, expect, it } from 'vitest';

import {
  categoryLabel,
  installMethods,
  isAgent,
  isSkill,
  languageLabel,
  packageType,
  type Agent,
} from './catalog';

function entry(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'web-research',
    name: 'web-research',
    description: 'Search the web',
    category: 'skills',
    latest_version: '0.1.0',
    icon: 'wrench',
    language: 'markdown',
    author: 'AMD',
    security_tier: 'experimental',
    download_size_bytes: 1024,
    tags: [],
    tools_count: 0,
    models: [],
    min_gaia_version: '',
    permissions: [],
    deprecated: false,
    requirements: {
      min_memory_gb: 0,
      min_disk_gb: 0,
      min_context_size: 0,
      platforms: [],
      npu: 'optional',
      gpu_vram_gb: 0,
    },
    readme: '',
    ...overrides,
  };
}

describe('lane discriminator', () => {
  it('treats an entry with no type as an agent (pre-#1716 catalogs)', () => {
    const a = entry({ type: undefined });
    expect(packageType(a)).toBe('agent');
    expect(isAgent(a)).toBe(true);
    expect(isSkill(a)).toBe(false);
  });

  it.each(['agent', 'app', 'component'] as const)('does not treat %s as a skill', (type) => {
    expect(isSkill(entry({ type }))).toBe(false);
  });

  it('recognises the skill lane', () => {
    const s = entry({ type: 'skill' });
    expect(packageType(s)).toBe('skill');
    expect(isSkill(s)).toBe(true);
    // A skill is NOT an agent — the agent listing must exclude it.
    expect(isAgent(s)).toBe(false);
  });
});

describe('installMethods', () => {
  it('offers `gaia skill install` for a skill, never the agent installer', () => {
    const methods = installMethods(entry({ type: 'skill' }));
    expect(methods).toHaveLength(1);
    expect(methods[0].command).toBe('gaia skill install web-research');
    expect(methods.some((m) => m.command.includes('gaia agent install'))).toBe(false);
    expect(methods.some((m) => m.command.startsWith('pip install'))).toBe(false);
  });

  it('warns that an experimental skill needs an explicit opt-in', () => {
    expect(installMethods(entry({ type: 'skill', security_tier: 'experimental' }))[0].note).toContain(
      '--allow-experimental',
    );
    expect(installMethods(entry({ type: 'skill', security_tier: 'verified' }))[0].note).not.toContain(
      '--allow-experimental',
    );
  });

  it('leaves the agent lane on the agent installer', () => {
    const methods = installMethods(entry({ id: 'chat', type: 'agent', language: 'python' }));
    expect(methods[0].command).toBe('gaia agent install chat');
  });
});

describe('display labels', () => {
  it('labels the skills category and the markdown language', () => {
    expect(categoryLabel('skills')).toBe('Skills');
    expect(languageLabel('markdown')).toBe('Markdown');
  });
});
