// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, describe, expect, it, vi } from 'vitest';

// Every download link on the home page is this function's `url`. The manifest
// ships no `url` field — it ships `path`, the hub's own object key — so the
// only question is whether the site uses it or re-derives the same shape by
// hand. Re-deriving means a CDN move or a per-OS subdirectory 404s every link
// while the build stays green.
describe('getComponentRelease — the download URL is the hub’s own path', () => {
  const HUB = 'https://hub.test';
  const originalHubUrl = process.env.HUB_CATALOG_URL;

  afterEach(() => {
    vi.unstubAllGlobals();
    if (originalHubUrl === undefined) delete process.env.HUB_CATALOG_URL;
    else process.env.HUB_CATALOG_URL = originalHubUrl;
  });

  const artifact = (filename: string, path?: string) => ({
    filename,
    ...(path === undefined ? {} : { path }),
    size_bytes: 1024,
    sha256: 'a'.repeat(64),
    content_type: 'application/octet-stream',
  });

  /** A fresh catalog module served one manifest. */
  async function loadServing(version: string, artifacts: unknown[]) {
    vi.resetModules();
    process.env.HUB_CATALOG_URL = HUB;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              latest_version: version,
              versions: { [version]: { artifacts } },
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          ),
      ),
    );
    return import('./catalog');
  }

  it('downloads from the path the manifest published, not a rebuilt one', async () => {
    const mod = await loadServing('0.24.1', [
      artifact('gaia-win-x64.exe', 'cdn/mirror-eu/win/gaia-win-x64.exe'),
      artifact('gaia-linux-x64', 'agents/terminal-hub/0.24.1/gaia-linux-x64'),
    ]);
    const { binaries } = await mod.getComponentRelease('terminal-hub');
    expect(binaries.map((b) => b.url)).toEqual([
      `${HUB}/cdn/mirror-eu/win/gaia-win-x64.exe`,
      `${HUB}/agents/terminal-hub/0.24.1/gaia-linux-x64`,
    ]);
  });

  it('joins an absolute path without doubling the slash', async () => {
    const mod = await loadServing('0.24.1', [
      artifact('gaia-linux-x64', '/agents/terminal-hub/0.24.1/gaia-linux-x64'),
    ]);
    const { binaries } = await mod.getComponentRelease('terminal-hub');
    expect(binaries[0].url).toBe(`${HUB}/agents/terminal-hub/0.24.1/gaia-linux-x64`);
  });

  // manifest.schema.json requires `path` on every artifact, so its absence is a
  // broken publish — and guessing the key is how every link 404s quietly.
  it('refuses to guess an object key, naming the artifact that lacks one', async () => {
    const mod = await loadServing('0.24.1', [artifact('gaia-linux-x64')]);
    await expect(mod.getComponentRelease('terminal-hub')).rejects.toThrow(
      /'terminal-hub' artifact 'gaia-linux-x64' at 0\.24\.1 publishes no 'path'/,
    );
  });

  it('keeps the filename, size and digest the manifest published', async () => {
    const mod = await loadServing('0.24.1', [
      artifact('gaia-linux-x64', 'agents/terminal-hub/0.24.1/gaia-linux-x64'),
    ]);
    const { version, binaries } = await mod.getComponentRelease('terminal-hub');
    expect(version).toBe('0.24.1');
    expect(binaries[0]).toEqual({
      filename: 'gaia-linux-x64',
      size_bytes: 1024,
      sha256: 'a'.repeat(64),
      url: `${HUB}/agents/terminal-hub/0.24.1/gaia-linux-x64`,
    });
  });
});
