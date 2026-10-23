// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Pure helpers tested directly; getRepoStats/getReleaseStats/getNpmDownloadStats
// hit the network so only their pagination/error-shape behavior is covered
// here, with fetch stubbed.

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  formatCount,
  formatRelativeDate,
  isMainReleaseTag,
  isReleaseCandidateTag,
  pickReleaseCandidate,
  pickStableRelease,
  streamReleases,
  sumAssetDownloads,
  type GithubRelease,
} from './github';

describe('isMainReleaseTag', () => {
  it('matches a plain semver tag', () => {
    expect(isMainReleaseTag('v1.2.3')).toBe(true);
    expect(isMainReleaseTag('v0.24.1')).toBe(true);
  });

  it('matches a 4-segment hotfix tag', () => {
    expect(isMainReleaseTag('v0.15.4.1')).toBe(true);
  });

  it('rejects sub-package release tags', () => {
    expect(isMainReleaseTag('agent-pkg-email-v0.5.0')).toBe(false);
  });

  it('rejects a partial version', () => {
    expect(isMainReleaseTag('v1.2')).toBe(false);
  });

  it('rejects a tag with no leading v', () => {
    expect(isMainReleaseTag('1.2.3')).toBe(false);
  });
});

describe('sumAssetDownloads', () => {
  it('sums an empty list to zero', () => {
    expect(sumAssetDownloads([])).toBe(0);
  });

  it('sums a single asset', () => {
    expect(sumAssetDownloads([{ download_count: 42 }])).toBe(42);
  });

  it('sums multiple assets', () => {
    expect(
      sumAssetDownloads([{ download_count: 10 }, { download_count: 5 }, { download_count: 0 }]),
    ).toBe(15);
  });
});

describe('formatRelativeDate', () => {
  const now = new Date('2026-09-17T12:00:00Z');

  it('labels the same calendar day as Today', () => {
    expect(formatRelativeDate(new Date('2026-09-17T01:00:00Z'), now)).toBe('Today');
  });

  it('labels one day back as "1 day ago"', () => {
    expect(formatRelativeDate(new Date('2026-09-16T01:00:00Z'), now)).toBe('1 day ago');
  });

  it('labels N days back as "N days ago"', () => {
    expect(formatRelativeDate(new Date('2026-09-05T01:00:00Z'), now)).toBe('12 days ago');
  });

  it('is deterministic for the same inputs', () => {
    const date = new Date('2026-09-01T00:00:00Z');
    expect(formatRelativeDate(date, now)).toBe(formatRelativeDate(date, now));
  });
});

describe('formatCount', () => {
  it('leaves small counts as plain integers', () => {
    expect(formatCount(950)).toBe('950');
  });

  it('abbreviates thousands with one decimal', () => {
    expect(formatCount(1200)).toBe('1.2k');
    expect(formatCount(15000)).toBe('15k');
  });

  it('abbreviates millions', () => {
    expect(formatCount(1_000_000)).toBe('1M');
    expect(formatCount(2_500_000)).toBe('2.5M');
  });
});

describe('getReleaseStats — pagination and error handling', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  function release(tag: string, overrides: Partial<Record<string, unknown>> = {}) {
    return {
      tag_name: tag,
      draft: false,
      prerelease: false,
      published_at: '2026-09-16T08:14:13Z',
      html_url: `https://github.com/amd/gaia/releases/tag/${tag}`,
      assets: [{ download_count: 1 }],
      ...overrides,
    };
  }

  it('stops paginating once a page comes back short', async () => {
    vi.resetModules();
    const page1 = Array.from({ length: 100 }, (_, i) => release(`v1.0.${i}`));
    const page2 = [release('v2.0.0')];
    const fetchMock = vi.fn(async (input: string | URL) => {
      const url = String(input);
      const body = url.includes('page=2') ? page2 : page1;
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const mod = await import('./github');
    const stats = await mod.getReleaseStats();

    expect(stats.totalReleaseCount).toBe(101);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('throws a [github]-prefixed error naming the endpoint on a non-200 response', async () => {
    vi.resetModules();
    const fetchMock = vi.fn(
      async () => new Response('rate limited', { status: 403, statusText: 'Forbidden' }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const mod = await import('./github');
    await expect(mod.getReleaseStats()).rejects.toThrow(/\[github\].*releases.*HTTP 403/s);
  });
});

describe('pickReleaseCandidate', () => {
  const asset = (name: string) => ({
    name,
    browser_download_url: `https://github.com/amd/gaia/releases/download/x/${name}`,
    size: 100_000_000,
    download_count: 0,
  });

  function release(tag: string, prerelease: boolean, overrides: Partial<GithubRelease> = {}): GithubRelease {
    return {
      tag_name: tag,
      draft: false,
      prerelease,
      published_at: '2026-10-01T00:00:00Z',
      html_url: `https://github.com/amd/gaia/releases/tag/${tag}`,
      assets: [],
      ...overrides,
    };
  }

  const STABLE = release('v0.24.1', false);
  const RC1 = release('v0.25.0-rc1', true, {
    assets: [
      asset('gaia-agent-ui-0.25.0-rc.1-x64-setup.exe'),
      asset('gaia-0.25.0-rc.1-win-x64-setup.exe'),
      asset('gaia-agent-ui-0.25.0-rc.1-arm64.dmg'),
      asset('gaia-agent-ui-0.25.0-rc.1-arm64.dmg.blockmap'),
      asset('gaia-agent-ui-0.25.0-rc.1-x86_64.AppImage'),
      asset('gaia-agent-ui-0.25.0-rc.1-amd64.deb'),
      asset('amd_gaia-0.25.0rc1-py3-none-any.whl'),
      asset('gaia-linux-amd64'),
    ],
  });

  it('recognises only the vX.Y.Z-rcN tag form', () => {
    expect(isReleaseCandidateTag('v0.25.0-rc1')).toBe(true);
    expect(isReleaseCandidateTag('v0.25.0-rc12')).toBe(true);
    expect(isReleaseCandidateTag('v0.25.0')).toBe(false);
    expect(isReleaseCandidateTag('v0.25.0-rc0')).toBe(false);
    expect(isReleaseCandidateTag('v0.25.0-beta1')).toBe(false);
    // The release stream counts main releases only; an RC is never one.
    expect(isMainReleaseTag('v0.25.0-rc1')).toBe(false);
  });

  it('offers a candidate newer than the latest stable, with its desktop installers', () => {
    const rc = pickReleaseCandidate([RC1, STABLE]);
    expect(rc).not.toBeNull();
    expect(rc!.tag).toBe('v0.25.0-rc1');
    expect(rc!.version).toBe('0.25.0');
    expect(rc!.htmlUrl).toBe('https://github.com/amd/gaia/releases/tag/v0.25.0-rc1');
    // Installers in a fixed order; the blockmap, wheel and raw binary are left
    // to the release page.
    // The one Windows setup leads even though it is listed after the desktop
    // app's own setup on the release.
    expect(rc!.downloads.map((d) => d.url.split('/').at(-1))).toEqual([
      'gaia-0.25.0-rc.1-win-x64-setup.exe',
      'gaia-agent-ui-0.25.0-rc.1-x64-setup.exe',
      'gaia-agent-ui-0.25.0-rc.1-arm64.dmg',
      'gaia-agent-ui-0.25.0-rc.1-x86_64.AppImage',
      'gaia-agent-ui-0.25.0-rc.1-amd64.deb',
    ]);
  });

  it('is hidden once the final for that version has shipped', () => {
    expect(pickReleaseCandidate([release('v0.25.0', false), RC1, STABLE])).toBeNull();
  });

  it('is hidden when there is no release candidate at all', () => {
    expect(pickReleaseCandidate([STABLE, release('v0.24.0', false)])).toBeNull();
  });

  it('picks the newest candidate by version, not by list order', () => {
    const rc2 = release('v0.25.0-rc2', true);
    const rc10 = release('v0.25.0-rc10', true);
    expect(pickReleaseCandidate([RC1, rc10, rc2, STABLE])!.tag).toBe('v0.25.0-rc10');
  });

  it('ignores drafts, and RC-named releases not marked as pre-releases', () => {
    expect(pickReleaseCandidate([{ ...RC1, draft: true }, STABLE])).toBeNull();
    expect(pickReleaseCandidate([{ ...RC1, prerelease: false }, STABLE])).toBeNull();
  });

  it('compares against a four-part hotfix as a newer stable', () => {
    const hotfix = release('v0.25.0.1', false);
    expect(pickReleaseCandidate([hotfix, RC1])).toBeNull();
  });

  it('still offers a candidate with no installers attached, via its release page', () => {
    const bare = release('v0.25.0-rc1', true);
    const rc = pickReleaseCandidate([bare, STABLE]);
    expect(rc!.downloads).toEqual([]);
    expect(rc!.htmlUrl).toContain('v0.25.0-rc1');
  });
});

describe('streamReleases', () => {
  function release(tag: string, prerelease: boolean, publishedAt: string, draft = false): GithubRelease {
    return {
      tag_name: tag,
      draft,
      prerelease,
      published_at: publishedAt,
      html_url: `https://github.com/amd/gaia/releases/tag/${tag}`,
      assets: [],
    };
  }

  const V0241 = release('v0.24.1', false, '2026-09-16T08:00:00Z');
  const V0230 = release('v0.23.0', false, '2026-08-13T08:00:00Z');
  const RC2 = release('v0.25.0-rc2', true, '2026-10-10T08:00:00Z');
  const RC12 = release('v0.25.0-rc12', true, '2026-10-13T08:00:00Z');

  it('puts the newest open release candidate in the stream, marked as an RC', () => {
    expect(streamReleases([V0230, RC2, V0241, RC12]).map((r) => [r.tag, r.kind])).toEqual([
      ['v0.25.0-rc12', 'rc'],
      ['v0.24.1', 'stable'],
      ['v0.23.0', 'stable'],
    ]);
  });

  // Newest by version, not by list order or date: rc10 sorts after rc9.
  it('keeps one candidate per upcoming version, chosen by rc number', () => {
    const rc9 = release('v0.25.0-rc9', true, '2026-10-12T08:00:00Z');
    const rc10 = release('v0.25.0-rc10', true, '2026-10-12T09:00:00Z');
    const next = release('v0.26.0-rc1', true, '2026-10-14T08:00:00Z');
    expect(streamReleases([rc10, next, rc9, V0241]).map((r) => r.tag)).toEqual([
      'v0.26.0-rc1',
      'v0.25.0-rc10',
      'v0.24.1',
    ]);
  });

  // Once v0.25.0 ships its candidates are history; twelve of them would bury
  // the releases the stream exists to show.
  it('drops candidates for a version that has since shipped', () => {
    const shipped = release('v0.25.0', false, '2026-10-20T08:00:00Z');
    expect(streamReleases([shipped, RC12, RC2, V0241]).map((r) => r.tag)).toEqual([
      'v0.25.0',
      'v0.24.1',
    ]);
  });

  it('leaves out drafts, sub-package releases and RC-named releases not marked pre-release', () => {
    const tags = streamReleases([
      V0241,
      release('v0.25.0-rc13', true, '2026-10-14T08:00:00Z', true),
      release('agent-pkg-email-v0.5.0', true, '2026-07-19T00:00:00Z'),
      release('v0.25.0-rc3', false, '2026-10-11T08:00:00Z'),
    ]).map((r) => r.tag);
    expect(tags).toEqual(['v0.24.1']);
  });
});

describe('pickStableRelease', () => {
  const asset = (tag: string, name: string) => ({
    name,
    browser_download_url: `https://github.com/amd/gaia/releases/download/${tag}/${name}`,
    size: 1234,
    download_count: 0,
  });

  function release(tag: string, opts: Partial<GithubRelease> = {}): GithubRelease {
    return {
      tag_name: tag,
      draft: false,
      prerelease: false,
      published_at: '2026-10-01T00:00:00Z',
      html_url: `https://github.com/amd/gaia/releases/tag/${tag}`,
      assets: [asset(tag, `gaia-agent-ui-${tag.slice(1)}-x64-setup.exe`)],
      ...opts,
    };
  }

  it('takes the newest stable release, with its assets as published', () => {
    const picked = pickStableRelease([release('v0.24.1'), release('v0.25.0')]);
    expect(picked).toEqual({
      tag: 'v0.25.0',
      version: '0.25.0',
      assets: [
        {
          name: 'gaia-agent-ui-0.25.0-x64-setup.exe',
          url: 'https://github.com/amd/gaia/releases/download/v0.25.0/gaia-agent-ui-0.25.0-x64-setup.exe',
          sizeBytes: 1234,
        },
      ],
    });
  });

  // The candidate has its own section; the main buttons stay on the release
  // that shipped, however new the candidate is.
  it('skips pre-releases and drafts, however new', () => {
    const picked = pickStableRelease([
      release('v0.25.1-rc2', { prerelease: true }),
      release('v0.26.0', { draft: true }),
      release('v0.25.0'),
    ]);
    expect(picked?.tag).toBe('v0.25.0');
  });

  it('skips sub-package releases and anything not marked pre-release but RC-named', () => {
    const picked = pickStableRelease([
      release('agent-pkg-email-v9.0.0'),
      release('v0.25.1-rc3'),
      release('v0.25.0'),
    ]);
    expect(picked?.tag).toBe('v0.25.0');
  });

  // A hotfix to an older line published later must not take over the buttons.
  it('orders by version, not by list position or publish date', () => {
    const picked = pickStableRelease([
      release('v0.24.2', { published_at: '2026-10-20T00:00:00Z' }),
      release('v0.25.0', { published_at: '2026-10-18T00:00:00Z' }),
      release('v0.25.0.1', { published_at: '2026-10-19T00:00:00Z' }),
    ]);
    expect(picked?.tag).toBe('v0.25.0.1');
  });

  it('is null when nothing stable has shipped', () => {
    expect(pickStableRelease([release('v0.25.0-rc1', { prerelease: true })])).toBeNull();
    expect(pickStableRelease([])).toBeNull();
  });
});

describe('getStableRelease', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('fails loudly, naming the fix, when the GitHub API is rate limited', async () => {
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 403 })));
    const mod = await import('./github');
    await expect(mod.getStableRelease()).rejects.toThrow(/HTTP 403.*set GITHUB_TOKEN/s);
  });

  it('fails loudly when the repo has no stable release', async () => {
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn(async () => Response.json([])));
    const mod = await import('./github');
    await expect(mod.getStableRelease()).rejects.toThrow(/no published stable release/);
  });
});
