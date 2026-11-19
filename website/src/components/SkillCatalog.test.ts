// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// What /hub renders for an empty skills lane and for a populated one, through
// Astro's container API so no network is involved.

import { experimental_AstroContainer } from 'astro/container';
import { describe, expect, it } from 'vitest';

import SkillCatalog from './SkillCatalog.astro';
import { toHubSkill, type HubSkill } from '../data/hub';

async function render(skills: HubSkill[]): Promise<string> {
  const container = await experimental_AstroContainer.create();
  return container.renderToString(SkillCatalog, { props: { skills } });
}

describe('SkillCatalog.astro', () => {
  it('says no skills are published and links the source tree when empty', async () => {
    const html = await render([]);
    expect(html).toContain('data-skills-empty');
    expect(html).toContain('No skills published yet');
    expect(html).toContain('href="https://github.com/amd/gaia/tree/main/hub/skills"');
    expect(html).not.toContain('data-skill=');
  });

  it('renders one card per skill with its version, tier, command and source', async () => {
    const skills = [
      toHubSkill({ id: 'summarize', name: 'summarize', description: 'Summarize a document.', type: 'skill', latest_version: '1.2.0', security_tier: 'verified' }),
      toHubSkill({ id: 'coding', name: 'coding', description: 'Write code.', type: 'skill', latest_version: '0.1.0', security_tier: 'experimental' }),
    ];
    const html = await render(skills);

    expect(html).not.toContain('No skills published yet');
    expect(html.match(/data-skill="/g)).toHaveLength(2);
    expect(html).toContain('Summarize a document.');
    expect(html).toContain('v1.2.0');
    expect(html).toContain('>verified<');
    expect(html).toContain('>experimental<');
    expect(html).toContain('gaia skill install summarize<');
    expect(html).toContain('gaia skill install coding --allow-experimental<');
    expect(html).toContain('href="https://github.com/amd/gaia/tree/main/hub/skills/coding"');
  });
});
