// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// What the release-candidate entry RENDERS. pickReleaseCandidate decides
// whether there is a candidate (src/data/github.test.ts); this proves the
// markup then stays quiet — nothing at all without one, and collapsed with one.

import { describe, expect, it } from 'vitest';

import type { ReleaseCandidate as Candidate } from '../data/github';

const CANDIDATE: Candidate = {
  tag: 'v0.25.0-rc1',
  version: '0.25.0',
  htmlUrl: 'https://github.com/amd/gaia/releases/tag/v0.25.0-rc1',
  downloads: [
    {
      label: 'Windows — desktop app (.exe)',
      url: 'https://github.com/amd/gaia/releases/download/v0.25.0-rc1/gaia-agent-ui-0.25.0-rc.1-x64-setup.exe',
      sizeBytes: 120 * 1024 * 1024,
    },
  ],
};

async function render(candidate: Candidate | null): Promise<string> {
  const { experimental_AstroContainer } = await import('astro/container');
  const ReleaseCandidate = (await import('./ReleaseCandidate.astro')).default;
  const container = await experimental_AstroContainer.create();
  return container.renderToString(ReleaseCandidate, { props: { candidate } });
}

describe('ReleaseCandidate.astro', () => {
  it('renders nothing when there is no candidate', async () => {
    expect((await render(null)).trim()).toBe('');
  });

  it('renders one collapsed entry with the downloads and both install commands', async () => {
    const html = await render(CANDIDATE);

    expect(html.match(/<details/g)).toHaveLength(1);
    // Collapsed: `open` would put it on equal footing with the stable downloads.
    expect(html).not.toMatch(/<details[^>]*\sopen[\s>=]/);
    expect(html).toContain('Try the release candidate · v0.25.0-rc1');

    expect(html).toContain(`href="${CANDIDATE.downloads[0].url}"`);
    expect(html).toContain(`href="${CANDIDATE.htmlUrl}"`);
    expect(html).toContain('pip install --pre amd-gaia');
    expect(html).toContain('npm i -g @amd-gaia/agent-ui@next');
  });

  it('keeps the summary small and muted, not a label like the stable downloads', async () => {
    const summary = (await render(CANDIDATE)).match(/<summary[^>]*>/)?.[0] ?? '';
    expect(summary).toContain('text-g-muted');
    expect(summary).toContain('text-[12px]');
    expect(summary).not.toContain('g-label');
  });

  it('leaves out the download list when the release has no installers', async () => {
    const html = await render({ ...CANDIDATE, downloads: [] });
    expect(html).not.toContain('<ul');
    expect(html).toContain(`href="${CANDIDATE.htmlUrl}"`);
  });
});
