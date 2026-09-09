// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Pure catalog helpers — the lane discriminator and the per-lane install
// command. No network: loadCatalog() is not exercised here.

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  categoryLabel,
  installCta,
  installMethods,
  isAgent,
  isNpmSidecar,
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

  it('offers GAIA first for an npm agent, then npm', () => {
    const methods = installMethods(
      entry({ id: 'email', type: 'agent', language: 'typescript', npm_package: '@amd-gaia/agent-email' }),
    );
    expect(methods[0].key).toBe('gaia');
    expect(methods[0].command).toBe('gaia agent install email');
    expect(methods[1].key).toBe('npm');
    expect(methods[1].command).toBe('npm i @amd-gaia/agent-email');
  });

  it('offers a platform download plus the global CLI for a non-agent with an npm package', () => {
    const methods = installMethods(entry({ id: 'gaia-ui', type: 'app', npm_package: 'gaia-ui' }));
    expect(methods.map((m) => m.key)).toEqual(['download', 'npm']);
    expect(methods[1].command).toBe('npm install -g gaia-ui');
    // A component/app is never installed INTO the GAIA app.
    expect(methods.some((m) => m.key === 'gaia')).toBe(false);
  });
});

// The packaging text has to describe the entry's own install path. An app or
// component that ships an npm CLI was calling itself a "frozen sidecar" — an
// agent-lane word its own install command never offers (#3298).
describe('isNpmSidecar — the packaging text matches the commands', () => {
  it('recognises the npm agent lane', () => {
    expect(isNpmSidecar(entry({ type: 'agent', npm_package: '@amd-gaia/agent-email' }))).toBe(true);
    expect(isNpmSidecar(entry({ type: undefined, npm_package: '@amd-gaia/agent-email' }))).toBe(true);
  });

  it.each(['app', 'component'] as const)(
    'is false for a %s with an npm package — it installs as a global CLI',
    (type) => {
      expect(isNpmSidecar(entry({ type, npm_package: '@amd-gaia/agent-ui' }))).toBe(false);
    },
  );

  it('is false for a skill with an npm package — its card shows no npm command', () => {
    expect(isNpmSidecar(entry({ type: 'skill', npm_package: 'gaia-skill' }))).toBe(false);
  });

  it.each(['agent', 'app', 'component', 'skill', undefined] as const)(
    'is false for a %s with no npm package',
    (type) => {
      expect(isNpmSidecar(entry({ type }))).toBe(false);
    },
  );
});

// The buttons under the install command have to offer the same paths the
// command does — a component/app that ships an npm CLI got an "Open in GAIA"
// primary its own card had just ruled out (#3231).
describe('installCta — the buttons match the commands', () => {
  it('keeps GAIA first and npm second for an npm agent', () => {
    expect(installCta(entry({ type: 'agent', npm_package: '@amd-gaia/agent-email' }))).toBe('gaia+npm');
  });

  it.each(['app', 'component'] as const)(
    'drops the GAIA deep link for a %s with an npm package',
    (type) => {
      expect(installCta(entry({ type, npm_package: 'gaia-ui' }))).toBe('npm');
    },
  );

  it.each(['agent', 'app', 'component', 'skill', undefined] as const)(
    'offers GAIA alone when a %s has no npm package',
    (type) => {
      expect(installCta(entry({ type }))).toBe('gaia');
    },
  );

  it('keeps a skill on the GAIA installer alone — its card shows no npm command', () => {
    expect(installCta(entry({ type: 'skill', npm_package: 'gaia-skill' }))).toBe('gaia');
  });
});

describe('display labels', () => {
  it('labels the skills category and the markdown language', () => {
    expect(categoryLabel('skills')).toBe('Skills');
    expect(languageLabel('markdown')).toBe('Markdown');
  });
});

// The lane split the Hub page actually renders. getAgentPackages() is what
// keeps a skill out of the agent listing on the website — the third and last
// layer of that invariant (the Python client and the in-app Hub page are the
// other two), and the only one that reaches the network, so it needs the
// catalog loader stubbed rather than a pure-helper call.
describe('getAgentPackages / getSkills — the rendered lane split', () => {
  const HUB = 'https://hub.test';
  const originalHubUrl = process.env.HUB_CATALOG_URL;

  afterEach(() => {
    vi.unstubAllGlobals();
    if (originalHubUrl === undefined) delete process.env.HUB_CATALOG_URL;
    else process.env.HUB_CATALOG_URL = originalHubUrl;
  });

  /** Load a fresh catalog module served the given entries. */
  async function loadWith(entries: Partial<Agent>[]) {
    vi.resetModules();
    process.env.HUB_CATALOG_URL = HUB;
    const fetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response(
          JSON.stringify({
            schema_version: 1,
            generated_at: '2026-07-29T00:00:00.000Z',
            agents: entries.map((e) => entry(e)),
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
    );
    vi.stubGlobal('fetch', fetchMock);
    return { mod: await import('./catalog'), fetchMock };
  }

  const MIXED: Partial<Agent>[] = [
    { id: 'chat', name: 'Chat', type: 'agent', category: 'conversation' },
    { id: 'web-research', name: 'web-research', type: 'skill' },
    { id: 'studio', name: 'Studio', type: 'app' },
    { id: 'rag-kit', name: 'Rag Kit', type: 'component' },
    { id: 'legacy', name: 'Legacy', type: undefined },
    { id: 'incident-review', name: 'incident-review', type: 'skill' },
  ];

  it('never lets a skill into the agent listing', async () => {
    const { mod } = await loadWith(MIXED);
    const agents = await mod.getAgentPackages();
    expect(agents.map((a) => a.id).sort()).toEqual([
      'chat',
      'legacy',
      'rag-kit',
      'studio',
    ]);
    expect(agents.some((a) => a.type === 'skill')).toBe(false);
  });

  it('returns exactly the skills on the skills lane', async () => {
    const { mod } = await loadWith(MIXED);
    const skills = await mod.getSkills();
    expect(skills.map((s) => s.id).sort()).toEqual(['incident-review', 'web-research']);
    for (const skill of skills) {
      expect(skill.type).toBe('skill');
      // Every skill row offers the skill installer, never the agent one.
      expect(mod.installMethods(skill)[0].command).toBe(`gaia skill install ${skill.id}`);
    }
  });

  it('partitions the whole catalog between the two lanes, losing nothing', async () => {
    const { mod } = await loadWith(MIXED);
    const all = await mod.getCatalog();
    const agents = await mod.getAgentPackages();
    const skills = await mod.getSkills();

    expect(agents.length + skills.length).toBe(all.length);
    expect([...agents, ...skills].map((a) => a.id).sort()).toEqual(
      MIXED.map((e) => e.id).sort(),
    );
  });

  it('keeps an unknown lane out of the skills listing', async () => {
    // A newer hub can serve a lane this build predates. It may keep appearing
    // in the agent listing (the historical default), but it must never be
    // rendered as a skill — that would advertise `gaia skill install` for
    // something that is not a skill.
    const { mod } = await loadWith([
      { id: 'chat', type: 'agent' },
      { id: 'future', type: 'workflow' as never },
      { id: 'web-research', type: 'skill' },
    ]);
    expect((await mod.getSkills()).map((s) => s.id)).toEqual(['web-research']);
    expect((await mod.getAgentPackages()).map((a) => a.id).sort()).toEqual([
      'chat',
      'future',
    ]);
  });

  it('renders an older catalog with no skills at all as pure agents', async () => {
    const { mod } = await loadWith([
      { id: 'chat', type: undefined },
      { id: 'demo', type: undefined },
    ]);
    expect(await mod.getSkills()).toEqual([]);
    expect((await mod.getAgentPackages()).map((a) => a.id).sort()).toEqual(['chat', 'demo']);
  });

  it('fetches the catalog once per build across both lanes', async () => {
    const { mod, fetchMock } = await loadWith(MIXED);
    await mod.getAgentPackages();
    await mod.getSkills();
    await mod.getCatalog();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain(`${HUB}/index.json`);
  });
});
