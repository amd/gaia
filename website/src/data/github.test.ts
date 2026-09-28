// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Pure helpers tested directly; getRepoStats/getReleaseStats/getNpmDownloadStats
// hit the network so only their pagination/error-shape behavior is covered
// here, with fetch stubbed — same depth as catalog.test.ts.

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  formatCount,
  formatRelativeDate,
  isMainReleaseTag,
  sumAssetDownloads,
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
