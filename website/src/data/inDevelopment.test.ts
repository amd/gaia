// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { getInDevelopmentAgents } from './inDevelopment';
import { collectAgents } from '../../scripts/generate-in-development.mjs';

describe('getInDevelopmentAgents', () => {
  it('exposes every agent in the snapshot with complete fields', () => {
    const agents = getInDevelopmentAgents([]);
    expect(agents.length).toBeGreaterThan(5);
    for (const a of agents) {
      expect(a.id).not.toBe('');
      expect(a.name).not.toBe('');
      expect(a.description).not.toBe('');
      expect(a.category).not.toBe('');
      expect(a.icon).not.toBe('');
    }
  });

  it('takes the id from the directory, not the manifest', () => {
    // hub/agents/analyst declares `id: data`; the row must link/filter as "analyst".
    const ids = getInDevelopmentAgents([]).map((a) => a.id);
    expect(ids).toContain('analyst');
    expect(ids).not.toContain('data');
  });

  it('drops published agents, examples, and demos', () => {
    const agents = getInDevelopmentAgents(['email']);
    const ids = agents.map((a) => a.id);
    expect(ids).not.toContain('email');
    // Self-declared `category: examples` — excluded by rule, not by name.
    for (const example of ['hello-world', 'word-count', 'doc-search']) {
      expect(ids).not.toContain(example);
    }
    expect(agents.every((a) => a.category !== 'examples')).toBe(true);
    // The one demo that miscategorises itself, so it needs the name deny-list.
    expect(ids).not.toContain('connectors-demo');
  });

  it('emits no version or size, and sorts by name', () => {
    const agents = getInDevelopmentAgents(['email']);
    const keys = new Set(agents.flatMap((a) => Object.keys(a)));
    expect([...keys].sort()).toEqual(['category', 'description', 'icon', 'id', 'name']);
    expect(agents.map((a) => a.name)).toEqual([...agents.map((a) => a.name)].sort());
  });
});

// The snapshot exists because the Railway build context is website/ only. That
// makes staleness the failure mode to guard — CI runs `npm run check:agents`,
// and this asserts the same invariant at test time.
describe('in-development.json snapshot', () => {
  it('matches the agent manifests on disk', () => {
    expect(getInDevelopmentAgents([])).toEqual(collectAgents());
  });

  it('skips hub/agents directories that carry no manifest', () => {
    const hubAgents = path.resolve(__dirname, '../../../hub/agents');
    const withoutManifest = fs
      .readdirSync(hubAgents, { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .filter((e) => !fs.existsSync(path.join(hubAgents, e.name, 'python', 'gaia-agent.yaml')))
      .map((e) => e.name);
    const ids = getInDevelopmentAgents([]).map((a) => a.id);
    for (const name of withoutManifest) expect(ids).not.toContain(name);
  });
});
