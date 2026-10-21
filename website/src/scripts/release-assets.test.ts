// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Every asset name the site looks for has to be one the release pipeline
// attaches. A drift here fails no build: the button just disappears, because
// the site renders nothing for a name the release does not carry. So the names
// are read from the files that produce them rather than trusted.

import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import {
  DESKTOP_PLATFORMS,
  INSTALLERS_FOR,
  PLATFORM_ORDER,
  artifactFileName,
  desktopArtifactFileName,
  type InstallerKey,
} from './download-target';

const read = (url: URL) => readFileSync(url, 'utf8').replace(/\r\n/g, '\n');

const ARTIFACTS_PY = read(new URL('../../../installer/tui/artifacts.py', import.meta.url));
const BUILD_TUI = read(new URL('../../../.github/workflows/build_tui.yml', import.meta.url));
const RELEASE_COMPONENTS = read(
  new URL('../../../.github/workflows/release_components.yml', import.meta.url),
);

const V = '1.2.3';

/** The body of one top-level job in a workflow, up to the next job. */
function job(workflow: string, id: string): string {
  const start = workflow.indexOf(`\n  ${id}:\n`);
  expect(start, `release_components.yml has no '${id}' job`).toBeGreaterThan(-1);
  const rest = workflow.slice(start + 1);
  const next = rest.slice(1).search(/\n {2}[a-z][\w-]*:\n/);
  return next === -1 ? rest : rest.slice(0, next + 1);
}

describe('the terminal installers', () => {
  // installer/tui/artifacts.py is the one table the build, the R2 publish and
  // the GitHub attach all read; the site's names must be that table's.
  const table = [...ARTIFACTS_PY.matchAll(/\(\s*"([^"]*\{version\}[^"]*)",\s*"([a-z0-9-]+)"\s*\)/g)].map(
    ([, template, key]) => [key, template.replace('{version}', V)] as const,
  );

  it('still finds the installer table', () => {
    expect(table.length, 'no ("<template>", "<key>") pairs found in artifacts.py').toBeGreaterThan(0);
  });

  it('offers exactly the installers artifacts.py builds, under the same names', () => {
    const offered = PLATFORM_ORDER.flatMap((p) => INSTALLERS_FOR[p]).map(
      (key) => [key, artifactFileName(key as InstallerKey, V)] as const,
    );
    expect([...offered].sort()).toEqual([...table].sort());
  });

  it('are attached to the GitHub release, not only published to R2', () => {
    const attach = job(RELEASE_COMPONENTS, 'github-release-installers');
    expect(attach).toContain('installer/tui/artifacts.py publish-args');
    expect(attach).toMatch(/gh release upload "\$\{TAG\}"[^\n]*\n[^\n]*--clobber/);
    expect(attach).toContain('name: windows-setup');
    expect(attach).toContain('pattern: tui-installers-*');
    // The website redeploy has to wait for the attach, or it renders without them.
    expect(job(RELEASE_COMPONENTS, 'redeploy-website')).toMatch(
      /needs:[^\n]*github-release-installers/,
    );
  });
});

describe('the raw terminal binaries', () => {
  // build_tui.yml names each `gaia-<goos>-<goarch>[.exe]`; publish.yml attaches
  // those files as they are.
  it('are the names the build matrix produces', () => {
    expect(BUILD_TUI).toContain('-o ../bin/gaia-${{ matrix.goos }}-${{ matrix.goarch }}${EXT}');
    const built = [...BUILD_TUI.matchAll(/- goos: (\w+)\n\s+goarch: (\w+)/g)].map(
      ([, os, arch]) => `gaia-${os}-${arch}${os === 'windows' ? '.exe' : ''}`,
    );
    expect(built).toHaveLength(6);
    expect(PLATFORM_ORDER.map((p) => artifactFileName(p, V)).sort()).toEqual(built.sort());
  });
});

describe('the desktop app', () => {
  // The agent-ui job waits for these three on the release before republishing
  // them to R2, so a rename that reached electron-builder but not here fails
  // that job loudly — and this keeps the site on the same three names.
  it('looks for the three builds the release pipeline waits for', () => {
    const waited = [...job(RELEASE_COMPONENTS, 'agent-ui').matchAll(/want_\w+="([^"]+)"/g)].map(
      ([, name]) => name.replace('${VERSION}', V),
    );
    expect(waited).toHaveLength(3);
    expect(DESKTOP_PLATFORMS.map((p) => desktopArtifactFileName(p, V)).sort()).toEqual(waited.sort());
  });
});
