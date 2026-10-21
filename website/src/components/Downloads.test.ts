// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// What the card RENDERS, not what its helpers return.
//
// src/scripts/download-target.ts has its own suite, but a green helper says
// nothing about the markup: a hand-written href and a dropped `hidden` both
// live entirely in the .astro file, and neither is reachable from a helper
// test. This renders the component through Astro's container API and reads the
// HTML back, so those two failures are assertions rather than a code review.
//
// Container API rather than parsing a built dist/index.html: it needs no build
// step, lets the GitHub releases be a fixture instead of the live network, and
// fails at the component rather than at whichever page happened to embed it.

import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

import type { GithubRelease } from '../data/github';

const VERSION = '0.24.1';

// Asset names are spelled out rather than produced by artifactFileName: a test
// that asks the source for the name it expects cannot notice the source
// changing it. These are the names publish.yml and release_components.yml
// attach to a final release.
const DESKTOP_FILES = {
  'win-x64': 'gaia-agent-ui-0.24.1-x64-setup.exe',
  'darwin-arm64': 'gaia-agent-ui-0.24.1-arm64.dmg',
  'linux-x64': 'gaia-agent-ui-0.24.1-x86_64.AppImage',
} as const;

const TUI_FILES = {
  'win-x64': 'gaia-windows-amd64.exe',
  'win-arm64': 'gaia-windows-arm64.exe',
  'darwin-x64': 'gaia-darwin-amd64',
  'darwin-arm64': 'gaia-darwin-arm64',
  'linux-x64': 'gaia-linux-amd64',
  'linux-arm64': 'gaia-linux-arm64',
  'win-x64-setup': 'gaia-0.24.1-win-x64-setup.exe',
  'darwin-arm64-pkg': 'gaia-0.24.1-darwin-arm64.pkg',
  'darwin-x64-pkg': 'gaia-0.24.1-darwin-x64.pkg',
  'linux-x64-deb': 'gaia_0.24.1_amd64.deb',
  'linux-x64-rpm': 'gaia-0.24.1.x86_64.rpm',
} as const;

// Not the `releases/download/<tag>/<name>` shape GitHub happens to use today,
// so an href built from the tag and name instead of the asset's own
// browser_download_url fails here rather than in a visitor's 404.
const assetUrl = (tag: string, name: string) => `https://objects.test/${tag}/${name}`;

const desktopUrl = (platform: keyof typeof DESKTOP_FILES) =>
  assetUrl(`v${VERSION}`, DESKTOP_FILES[platform]);
const tuiUrl = (key: keyof typeof TUI_FILES) => assetUrl(`v${VERSION}`, TUI_FILES[key]);

const asset = (tag: string, name: string) => ({
  name,
  browser_download_url: assetUrl(tag, name),
  size: 98_765_432,
  download_count: 7,
});

function release(tag: string, names: readonly string[], prerelease = false): GithubRelease {
  return {
    tag_name: tag,
    draft: false,
    prerelease,
    published_at: '2026-09-16T08:00:00Z',
    html_url: `https://github.com/amd/gaia/releases/tag/${tag}`,
    assets: names.map((n) => asset(tag, n)),
  };
}

const stable = (version: string, names: readonly string[]) => release(`v${version}`, names);

// What the GitHub releases API returns, newest first. The pre-release is newer
// than the stable one and carries the same terminal names, so a page that took
// the newest release rather than the newest STABLE one links its files.
let RELEASES: GithubRelease[] = [];
const defaultReleases = (): GithubRelease[] => [
  release(
    'v0.25.0-rc1',
    ['gaia-agent-ui-0.25.0-rc.1-x64-setup.exe', ...Object.values(TUI_FILES)],
    true,
  ),
  stable(VERSION, [
    ...Object.values(DESKTOP_FILES),
    ...Object.values(TUI_FILES),
    // Attached beside the builds and matched by no key — the card has to
    // ignore them rather than offer an updater or checksum file as a download.
    'gaia-agent-ui-0.24.1-arm64.dmg.blockmap',
    'gaia-tui-SHA256SUMS.txt',
    'amd_gaia-0.24.1-py3-none-any.whl',
  ]),
  stable('0.24.0', ['gaia-agent-ui-0.24.0-x64-setup.exe']),
];

// ---- A minimal HTML reader -------------------------------------------------
//
// The site ships no DOM parser, and the only markup read here is Astro's own
// output for one file — well-formed, with no attribute value containing `>`.
// Counts are asserted everywhere below so a reader that finds nothing fails
// loudly instead of passing vacuously.

interface El {
  tag: string;
  attrs: Record<string, string | true>;
  children: (El | string)[];
  parent: El | null;
}

const VOID = new Set([
  'area',
  'base',
  'br',
  'col',
  'embed',
  'hr',
  'img',
  'input',
  'link',
  'meta',
  'param',
  'source',
  'track',
  'wbr',
]);

const TAG = /<(\/?)([a-zA-Z][\w:-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g;
const ATTR = /([a-zA-Z_:@][-\w:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;

function parseAttrs(raw: string): Record<string, string | true> {
  const out: Record<string, string | true> = {};
  for (const m of raw.replace(/\/\s*$/, '').matchAll(ATTR)) {
    out[m[1].toLowerCase()] = m[2] ?? m[3] ?? m[4] ?? true;
  }
  return out;
}

function parse(html: string): El {
  const root: El = { tag: '#root', attrs: {}, children: [], parent: null };
  let cur = root;
  let last = 0;
  for (const m of html.matchAll(TAG)) {
    const text = html.slice(last, m.index);
    if (text.trim()) cur.children.push(text);
    last = m.index + m[0].length;
    const [, closing, rawTag, rawAttrs] = m;
    const tag = rawTag.toLowerCase();
    if (closing) {
      let node: El | null = cur;
      while (node && node.tag !== tag) node = node.parent;
      if (node?.parent) cur = node.parent;
      continue;
    }
    const el: El = { tag, attrs: parseAttrs(rawAttrs), children: [], parent: cur };
    cur.children.push(el);
    if (!/\/\s*$/.test(rawAttrs) && !VOID.has(tag)) cur = el;
  }
  const tail = html.slice(last);
  if (tail.trim()) cur.children.push(tail);
  return root;
}

function descendants(el: El): El[] {
  const out: El[] = [];
  for (const child of el.children) {
    if (typeof child === 'string') continue;
    out.push(child, ...descendants(child));
  }
  return out;
}

/** Text as a screen reader would take it — aria-hidden subtrees dropped. */
function textOf(el: El): string {
  const parts: string[] = [];
  for (const child of el.children) {
    if (typeof child === 'string') parts.push(child);
    else if (child.attrs['aria-hidden'] !== 'true') parts.push(textOf(child));
  }
  return parts.join(' ').replace(/\s+/g, ' ').trim();
}

const hrefsIn = (el: El): string[] =>
  descendants(el)
    .filter((e) => e.tag === 'a' && typeof e.attrs.href === 'string')
    .map((e) => e.attrs.href as string);

// ---- Rendering -------------------------------------------------------------

function stubReleases(respond: () => Response) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string | URL) => {
      const url = String(input);
      if (!url.startsWith('https://api.github.com/repos/amd/gaia/releases?')) {
        throw new Error(`unexpected fetch in test: ${url}`);
      }
      return respond();
    }),
  );
}

async function renderHtml(): Promise<string> {
  vi.resetModules();
  const { experimental_AstroContainer } = await import('astro/container');
  const Downloads = (await import('./Downloads.astro')).default;
  const container = await experimental_AstroContainer.create();
  return container.renderToString(Downloads);
}

async function render() {
  stubReleases(() => Response.json(RELEASES));
  const html = await renderHtml();
  const { pickStableRelease } = await import('../data/github');
  return {
    html,
    root: parse(html),
    // The stable release's own asset URLs: the only hrefs the page may use.
    publishedUrls: new Set(pickStableRelease(RELEASES)!.assets.map((a) => a.url)),
  };
}

let rendered: Awaited<ReturnType<typeof render>>;

beforeEach(async () => {
  RELEASES = defaultReleases();
  rendered = await render();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const ctas = () => descendants(rendered.root).filter((e) => 'data-dl-cta' in e.attrs);

describe('Downloads.astro', () => {
  it('links only to assets of the newest stable release', () => {
    const links = descendants(rendered.root).filter(
      (e) => e.tag === 'a' && typeof e.attrs.href === 'string',
    );
    const invented = links
      .map((a) => a.attrs.href as string)
      .filter((href) => !rendered.publishedUrls.has(href));
    expect(invented).toEqual([]);

    // 2 desktop CTAs + 6 terminal CTAs + 2 + 9 in the all-downloads list.
    expect(links).toHaveLength(19);
    expect(rendered.publishedUrls.size).toBe(17);
    // Never the pre-release, though it is newer and carries the same names.
    expect(links.filter((a) => String(a.attrs.href).includes('-rc'))).toEqual([]);
  });

  // 0.24.1 predates the one Windows setup (its win-x64-setup is terminal-only),
  // so Windows x64 gets no download at all: never the desktop app's own setup
  // or the raw binary standing in for the setup.
  it('offers Windows x64 nothing when the release has no one-setup', () => {
    const hrefs = descendants(rendered.root)
      .filter((e) => e.tag === 'a')
      .map((a) => a.attrs.href as string);
    expect(hrefs).not.toContain(desktopUrl('win-x64'));
    expect(hrefs).not.toContain(tuiUrl('win-x64'));
    expect(hrefs).not.toContain(tuiUrl('win-x64-setup'));

    const windows = ctas().filter((b) => b.attrs['data-platform'] === 'win-x64');
    expect(windows.map((b) => [b.attrs['data-surface'], b.attrs['data-kind']])).toEqual([
      ['setup', 'setup-missing'],
    ]);
    expect(hrefsIn(windows[0])).toEqual([]);
    expect(textOf(windows[0])).toContain(`not published for v${VERSION}`);
  });

  it('keeps every reveal block hidden until the script places the machine', () => {
    const all = descendants(rendered.root);
    const name = (e: El) =>
      'data-dl-cta' in e.attrs
        ? `cta ${e.attrs['data-surface']}:${e.attrs['data-platform']}`
        : 'data-dl-note' in e.attrs
          ? `note ${e.attrs['data-dl-note']}`
          : `${e.tag}#${all.indexOf(e)}`;

    const hidden = all.filter((e) => 'hidden' in e.attrs).map(name);
    const reveals = all
      .filter((e) => 'data-dl-cta' in e.attrs || 'data-dl-note' in e.attrs)
      .map(name);

    // 5 desktop blocks (2 builds, 3 "no build"), 5 terminal rows, the Windows
    // "no setup" line, 2 captions.
    expect(reveals.length).toBe(13);
    expect(hidden.sort()).toEqual([...reveals].sort());

    // The full list is the no-JS offer, so it must render open.
    const list = all.filter((e) => 'data-dl-list' in e.attrs);
    expect(list.length).toBe(1);
    expect('hidden' in list[0].attrs).toBe(false);
  });

  it('offers each block only its own platform, and no build where there is none', () => {
    const expected: Record<string, string[]> = {
      // Windows x64's only offer is the one setup, which 0.24.1 does not have.
      'setup:win-x64': [],
      'desktop:darwin-arm64': [desktopUrl('darwin-arm64')],
      'desktop:linux-x64': [desktopUrl('linux-x64')],
      // The three the app publishes no build for: copy, never a link.
      'desktop:win-arm64': [],
      'desktop:darwin-x64': [],
      'desktop:linux-arm64': [],
      'tui:darwin-arm64': [tuiUrl('darwin-arm64-pkg')],
      'tui:linux-x64': [tuiUrl('linux-x64-deb'), tuiUrl('linux-x64-rpm')],
      'tui:win-arm64': [tuiUrl('win-arm64')],
      'tui:darwin-x64': [tuiUrl('darwin-x64-pkg')],
      'tui:linux-arm64': [tuiUrl('linux-arm64')],
    };

    const actual = Object.fromEntries(
      ctas().map((block) => [
        `${block.attrs['data-surface']}:${block.attrs['data-platform']}`,
        hrefsIn(block),
      ]),
    );
    expect(actual).toEqual(expected);
  });

  it('labels a lone terminal installer like the desktop button, and keeps Linux formats', () => {
    const label = (surface: string, platform: string) =>
      ctas()
        .filter((b) => b.attrs['data-surface'] === surface && b.attrs['data-platform'] === platform)
        .flatMap((b) => descendants(b).filter((e) => e.tag === 'a'))
        .map(textOf);

    expect(label('desktop', 'darwin-arm64')).toEqual(['Download for macOS (Apple Silicon)']);
    expect(label('tui', 'darwin-arm64')).toEqual(['Download for macOS (Apple Silicon)']);
    expect(label('tui', 'linux-x64')).toEqual([
      'Debian / Ubuntu (.deb)',
      'Fedora / RHEL / SUSE (.rpm)',
    ]);
  });

  it('names the platform in every link of the all-downloads list', () => {
    const [list] = descendants(rendered.root).filter((e) => 'data-dl-list' in e.attrs);
    const names = descendants(list)
      .filter((e) => e.tag === 'a')
      .map((a) => (typeof a.attrs['aria-label'] === 'string' ? a.attrs['aria-label'] : textOf(a)));

    expect(names).toHaveLength(11);
    // Six "Standalone binary" entries in a screen reader's links list are
    // indistinguishable; the platform is what tells them apart.
    expect(new Set(names).size).toBe(names.length);
    const platforms = [
      'Windows (x64)',
      'Windows (ARM64)',
      'macOS (Intel)',
      'macOS (Apple Silicon)',
      'Linux (x64)',
      'Linux (ARM64)',
    ];
    expect(names.filter((n) => platforms.some((p) => n.includes(p)))).toEqual(names);
  });

  it('spaces the blocks a Mac reveals together', () => {
    // Safari reports no architecture, so both Mac blocks unhide at once. The
    // wrappers carry no margin of their own; the container has to provide it.
    for (const block of ctas()) {
      expect(String(block.parent?.attrs.class ?? '')).toMatch(/\bspace-y-\d/);
    }
  });

  it('keeps the two cards under the region heading, not peer to the page', () => {
    const all = descendants(rendered.root);
    const h2s = all.filter((e) => e.tag === 'h2');
    expect(h2s).toHaveLength(1);
    expect(String(h2s[0].attrs.class ?? '')).toContain('sr-only');
    expect(all.filter((e) => e.tag === 'h3').map(textOf)).toEqual(['GAIA Agent UI', 'GAIA Terminal UI']);
  });
});

// ---- The one Windows setup (0.25.0 on) -------------------------------------
//
// From 0.25.0 the terminal's win-x64-setup is the setup that asks desktop
// app / terminal / both. Same filename pattern as the terminal-only 0.24.1
// setup above, which is why the card is gated on the version as well as on the
// file being on the release.

const SETUP_VERSION = '0.25.0';
const SETUP_DESKTOP_FILES = {
  'win-x64': 'gaia-agent-ui-0.25.0-x64-setup.exe',
  'darwin-arm64': 'gaia-agent-ui-0.25.0-arm64.dmg',
  'linux-x64': 'gaia-agent-ui-0.25.0-x86_64.AppImage',
} as const;
const SETUP_RELEASE_FILES = {
  'win-x64': 'gaia-windows-amd64.exe',
  'win-arm64': 'gaia-windows-arm64.exe',
  'darwin-x64': 'gaia-darwin-amd64',
  'darwin-arm64': 'gaia-darwin-arm64',
  'linux-x64': 'gaia-linux-amd64',
  'linux-arm64': 'gaia-linux-arm64',
  'win-x64-setup': 'gaia-0.25.0-win-x64-setup.exe',
  'darwin-arm64-pkg': 'gaia-0.25.0-darwin-arm64.pkg',
  'linux-x64-deb': 'gaia_0.25.0_amd64.deb',
} as const;

const setupUrl = (key: keyof typeof SETUP_RELEASE_FILES) =>
  assetUrl(`v${SETUP_VERSION}`, SETUP_RELEASE_FILES[key]);
const setupDesktopUrl = (platform: keyof typeof SETUP_DESKTOP_FILES) =>
  assetUrl(`v${SETUP_VERSION}`, SETUP_DESKTOP_FILES[platform]);

const releaseAt = (tuiFiles: readonly string[]) => [
  stable(SETUP_VERSION, [...Object.values(SETUP_DESKTOP_FILES), ...tuiFiles]),
];

describe('Downloads.astro with the one Windows setup', () => {
  it('offers Windows one setup instead of a card each', async () => {
    RELEASES = releaseAt(Object.values(SETUP_RELEASE_FILES));
    rendered = await render();
    const all = descendants(rendered.root);

    const setup = ctas().filter((b) => b.attrs['data-surface'] === 'setup');
    expect(setup).toHaveLength(1);
    expect(setup[0].attrs['data-platform']).toBe('win-x64');
    expect('hidden' in setup[0].attrs).toBe(true);
    expect(hrefsIn(setup[0])).toEqual([setupUrl('win-x64-setup')]);
    expect(String(setup[0].parent?.attrs.class ?? '')).toMatch(/\bspace-y-\d/);
    expect(textOf(setup[0])).toContain('the desktop app, the terminal, or both');

    // The choice is made in the setup, so neither card offers Windows x64 a button.
    const perSurfaceWindows = ctas().filter(
      (b) => b.attrs['data-surface'] !== 'setup' && b.attrs['data-platform'] === 'win-x64',
    );
    expect(perSurfaceWindows).toEqual([]);

    // The cards sit in a wrapper the script can hide; server-side it is shown.
    const cards = all.filter((e) => 'data-dl-cards' in e.attrs);
    expect(cards).toHaveLength(1);
    expect('hidden' in cards[0].attrs).toBe(false);
    expect(all.filter((e) => e.tag === 'h3').map(textOf)).toEqual([
      'GAIA for Windows',
      'GAIA Agent UI',
      'GAIA Terminal UI',
    ]);

    const invented = descendants(rendered.root)
      .filter((e) => e.tag === 'a' && typeof e.attrs.href === 'string')
      .map((a) => a.attrs.href as string)
      .filter((href) => !rendered.publishedUrls.has(href));
    expect(invented).toEqual([]);
  });

  // The desktop app's own setup and the raw binary stay on the release (the
  // auto-updater and install.ps1 fetch them) but are not offered on the page.
  it('lists the setup once, and nothing else for Windows x64', async () => {
    RELEASES = releaseAt(Object.values(SETUP_RELEASE_FILES));
    rendered = await render();
    const hrefs = descendants(rendered.root)
      .filter((e) => e.tag === 'a')
      .map((a) => a.attrs.href as string);
    const [list] = descendants(rendered.root).filter((e) => 'data-dl-list' in e.attrs);
    const listLinks = descendants(list).filter((e) => e.tag === 'a');

    expect(listLinks.filter((a) => a.attrs.href === setupUrl('win-x64-setup'))).toHaveLength(1);
    expect(hrefs).not.toContain(setupDesktopUrl('win-x64'));
    expect(hrefs).not.toContain(setupUrl('win-x64'));

    const names = listLinks.map((a) =>
      typeof a.attrs['aria-label'] === 'string' ? a.attrs['aria-label'] : textOf(a),
    );
    expect(new Set(names).size).toBe(names.length);
    expect(names.filter((n) => n.includes('Windows (x64)'))).toEqual([
      'GAIA setup for Windows (x64): desktop app, terminal, or both',
    ]);
  });

  // v0.25.0 as GitHub has it: no one-setup. Until a release carries it, Windows
  // x64 gets a plain line and no button -- not the desktop app's setup or the
  // raw binary in its place.
  it('offers no Windows x64 download when the release carries no setup', async () => {
    const { 'win-x64-setup': _setup, ...rest } = SETUP_RELEASE_FILES;
    RELEASES = releaseAt(Object.values(rest));
    rendered = await render();

    const windows = ctas().filter((b) => b.attrs['data-platform'] === 'win-x64');
    expect(windows.map((b) => [b.attrs['data-surface'], b.attrs['data-kind']])).toEqual([
      ['setup', 'setup-missing'],
    ]);
    expect(textOf(windows[0])).toContain(`not published for v${SETUP_VERSION}`);

    const hrefs = descendants(rendered.root)
      .filter((e) => e.tag === 'a')
      .map((a) => a.attrs.href as string);
    expect(hrefs).not.toContain(setupDesktopUrl('win-x64'));
    expect(hrefs).not.toContain(setupUrl('win-x64'));
    expect(hrefs.filter((h) => !rendered.publishedUrls.has(h))).toEqual([]);
  });
});

// ---- What the release does not carry ---------------------------------------

describe('Downloads.astro against an incomplete release', () => {
  // v0.25.0 as GitHub actually has it: the desktop app and the raw terminal
  // binaries, but none of the terminal installers (those went to the hub only).
  // Each missing installer is absent — never a link built from a name the
  // release does not carry.
  it('renders no button for an installer the release does not carry', async () => {
    const {
      'win-x64-setup': _setup,
      'darwin-arm64-pkg': _pkg,
      'linux-x64-deb': _deb,
      ...raw
    } = SETUP_RELEASE_FILES;
    RELEASES = releaseAt(Object.values(raw));
    rendered = await render();

    const tui = Object.fromEntries(
      ctas()
        .filter((b) => b.attrs['data-surface'] === 'tui')
        .map((b) => [b.attrs['data-platform'], [b.attrs['data-kind'], ...hrefsIn(b)]]),
    );
    expect(tui).toEqual({
      'darwin-arm64': ['binary', setupUrl('darwin-arm64')],
      'linux-x64': ['binary', setupUrl('linux-x64')],
      'win-arm64': ['binary', setupUrl('win-arm64')],
      'darwin-x64': ['binary', setupUrl('darwin-x64')],
      'linux-arm64': ['binary', setupUrl('linux-arm64')],
    });
    const hrefs = descendants(rendered.root)
      .filter((e) => e.tag === 'a')
      .map((a) => a.attrs.href as string);
    expect(hrefs.filter((h) => !rendered.publishedUrls.has(h))).toEqual([]);
    expect(hrefs.filter((h) => /\.(pkg|deb|rpm)$|win-x64-setup/.test(h))).toEqual([]);
  });

  it('drops a desktop platform the release does not carry, and keeps the rest', async () => {
    RELEASES = [
      stable(SETUP_VERSION, [
        SETUP_DESKTOP_FILES['linux-x64'],
        ...Object.values(SETUP_RELEASE_FILES),
      ]),
    ];
    rendered = await render();
    const desktop = ctas().filter(
      (b) => b.attrs['data-surface'] === 'desktop' && b.attrs['data-kind'] === 'build',
    );
    expect(desktop.map((b) => b.attrs['data-platform'])).toEqual(['linux-x64']);
  });

  it('fails the build when the stable release carries no desktop build', async () => {
    stubReleases(() => Response.json([stable(SETUP_VERSION, Object.values(SETUP_RELEASE_FILES))]));
    await expect(renderHtml()).rejects.toThrow(
      /v0\.25\.0 carries \d+ assets, none of which is a desktop app build/,
    );
  });

  // The Windows desktop setup is on the release but not offered, so on its own
  // it does not count: the desktop card would render with nothing in it.
  it('fails the build when the only desktop build is the one the page does not offer', async () => {
    stubReleases(() =>
      Response.json([
        stable(SETUP_VERSION, [
          SETUP_DESKTOP_FILES['win-x64'],
          ...Object.values(SETUP_RELEASE_FILES),
        ]),
      ]),
    );
    await expect(renderHtml()).rejects.toThrow(
      /v0\.25\.0 carries \d+ assets, none of which is a desktop app build/,
    );
  });

  it('fails the build when the stable release carries no terminal build', async () => {
    stubReleases(() => Response.json([stable(SETUP_VERSION, Object.values(SETUP_DESKTOP_FILES))]));
    await expect(renderHtml()).rejects.toThrow(
      /v0\.25\.0 carries \d+ assets, none of which is a terminal build/,
    );
  });

  // A release candidate is offered in its own section; it never stands in for
  // a missing stable release.
  it('fails the build when only pre-releases exist', async () => {
    stubReleases(() =>
      Response.json([release('v0.25.0-rc1', Object.values(SETUP_DESKTOP_FILES), true)]),
    );
    await expect(renderHtml()).rejects.toThrow(/no published stable release/);
  });

  it('fails the build when the GitHub API does', async () => {
    stubReleases(() => new Response('rate limited', { status: 403 }));
    await expect(renderHtml()).rejects.toThrow(/\[github\].*releases.*HTTP 403.*GITHUB_TOKEN/s);
  });
});
