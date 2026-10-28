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
const blockFor = (surface: string, platform: string) =>
  ctas().filter((b) => b.attrs['data-surface'] === surface && b.attrs['data-platform'] === platform);
const theList = () => {
  const lists = descendants(rendered.root).filter((e) => 'data-dl-list' in e.attrs);
  expect(lists).toHaveLength(1);
  return lists[0];
};

describe('Downloads.astro', () => {
  it('links only to assets of the newest stable release', () => {
    const links = descendants(rendered.root).filter(
      (e) => e.tag === 'a' && typeof e.attrs.href === 'string',
    );
    const invented = links
      .map((a) => a.attrs.href as string)
      .filter((href) => !rendered.publishedUrls.has(href));
    expect(invented).toEqual([]);

    // 3 desktop buttons + 6 terminal buttons + 3 + 11 in the folded list.
    expect(links).toHaveLength(23);
    expect(rendered.publishedUrls.size).toBe(17);
    // Never the pre-release, though it is newer and carries the same names.
    expect(links.filter((a) => String(a.attrs.href).includes('-rc'))).toEqual([]);
  });

  it('keeps every reveal block hidden until the script places the machine', () => {
    const all = descendants(rendered.root);
    const name = (e: El) =>
      'data-dl-cta' in e.attrs
        ? `cta ${e.attrs['data-surface']}:${e.attrs['data-platform']}`
        : `${e.tag}#${all.indexOf(e)}`;

    const hidden = all.filter((e) => 'hidden' in e.attrs).map(name);
    const reveals = all.filter((e) => 'data-dl-cta' in e.attrs).map(name);

    // 6 desktop blocks (3 builds, 3 "no build") and 6 terminal rows.
    expect(reveals.length).toBe(12);
    expect(hidden.sort()).toEqual([...reveals].sort());
  });

  it('folds every other file into a labelled disclosure that starts closed', () => {
    const list = theList();
    // A native <details> opens by keyboard and without JS.
    expect(list.tag).toBe('details');
    expect('open' in list.attrs).toBe(false);
    expect('hidden' in list.attrs).toBe(false);

    const summaries = descendants(list).filter((e) => e.tag === 'summary');
    expect(summaries).toHaveLength(1);
    expect(textOf(summaries[0])).toBe('Other platforms and installers');
    // A visible chevron beside the label.
    expect(descendants(summaries[0]).some((e) => e.attrs['aria-hidden'] === 'true')).toBe(true);
    expect(descendants(rendered.root).filter((e) => e.tag === 'details')).toHaveLength(1);
  });

  it('offers each block one button for its own platform, and none where there is no build', () => {
    const expected: Record<string, string[]> = {
      'desktop:win-x64': [desktopUrl('win-x64')],
      'desktop:darwin-arm64': [desktopUrl('darwin-arm64')],
      'desktop:linux-x64': [desktopUrl('linux-x64')],
      // The three the app publishes no build for: copy, never a link.
      'desktop:win-arm64': [],
      'desktop:darwin-x64': [],
      'desktop:linux-arm64': [],
      'tui:win-x64': [tuiUrl('win-x64-setup')],
      'tui:darwin-arm64': [tuiUrl('darwin-arm64-pkg')],
      // The .rpm is in the folded list.
      'tui:linux-x64': [tuiUrl('linux-x64-deb')],
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
    expect(hrefsIn(theList())).toContain(tuiUrl('linux-x64-rpm'));
  });

  it('puts each surface button under its own preview card', () => {
    const cards = descendants(rendered.root).filter((e) => e.tag === 'section');
    expect(cards.map((c) => textOf(descendants(c).find((e) => e.tag === 'h3')!))).toEqual([
      'GAIA Agent UI',
      'GAIA Terminal UI',
    ]);
    const surfaces = cards.map((c) => [
      ...new Set(descendants(c).filter((e) => 'data-dl-cta' in e.attrs).map((b) => b.attrs['data-surface'])),
    ]);
    expect(surfaces).toEqual([['desktop'], ['tui']]);
  });

  it('labels every card button by platform and captions what the file is', () => {
    const view = (surface: string, platform: string) =>
      blockFor(surface, platform).map((b) => ({
        button: descendants(b).filter((e) => e.tag === 'a').map(textOf),
        caption: textOf(descendants(b).find((e) => e.tag === 'p')!),
      }));

    expect(view('desktop', 'linux-x64')).toEqual([
      { button: ['Download for Linux (x64)'], caption: 'AppImage · v0.24.1 · 94.2 MB' },
    ]);
    expect(view('tui', 'linux-x64')).toEqual([
      {
        button: ['Download for Linux (x64)'],
        caption: 'Installer (.deb — Debian, Ubuntu) · v0.24.1 · 94.2 MB',
      },
    ]);
    expect(view('tui', 'linux-arm64')).toEqual([
      { button: ['Download for Linux (ARM64)'], caption: 'Standalone binary · v0.24.1 · 94.2 MB' },
    ]);
    expect(textOf(blockFor('desktop', 'darwin-x64')[0])).toBe('No desktop app for macOS (Intel). Use the terminal.');
  });

  it('names the platform in every link of the folded list', () => {
    const names = descendants(theList())
      .filter((e) => e.tag === 'a')
      .map((a) => (typeof a.attrs['aria-label'] === 'string' ? a.attrs['aria-label'] : textOf(a)));

    expect(names).toHaveLength(14);
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
    for (const block of ctas().filter((b) => b.attrs['data-surface'] !== 'setup')) {
      expect(String(block.parent?.attrs.class ?? '')).toMatch(/\bspace-y-\d/);
    }
  });

  it('keeps the two cards under the region heading, not peer to the page', () => {
    const all = descendants(rendered.root);
    const h2s = all.filter((e) => e.tag === 'h2');
    expect(h2s).toHaveLength(1);
    expect(String(h2s[0].attrs.class ?? '')).toContain('sr-only');
    expect(all.filter((e) => e.tag === 'h3').map(textOf)).toEqual([
      'GAIA Agent UI',
      'GAIA Terminal UI',
    ]);
  });

  // Before 0.25.0 the Windows setup was terminal-only: no one-installer block.
  it('offers no combined installer for a release before the setup asked', () => {
    expect(ctas().filter((b) => b.attrs['data-surface'] === 'setup')).toEqual([]);
  });
});

// ---- The one Windows setup (0.25.0 on) -------------------------------------
//
// From 0.25.0 the terminal's win-x64-setup is the setup that asks desktop
// app / terminal / both. Same filename pattern as the terminal-only 0.24.1
// setup above, which is why it is gated on the version as well as on the file
// being on the release.

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
  it('offers Windows x64 the one installer under both cards, and no per-card file', async () => {
    RELEASES = releaseAt(Object.values(SETUP_RELEASE_FILES));
    rendered = await render();

    const setup = blockFor('setup', 'win-x64');
    expect(setup).toHaveLength(1);
    expect('hidden' in setup[0].attrs).toBe(true);
    expect(hrefsIn(setup[0])).toEqual([setupUrl('win-x64-setup')]);
    expect(textOf(setup[0])).toContain('One installer for both apps');
    // Outside the two cards, not inside one of them.
    const cards = descendants(rendered.root).filter((e) => e.tag === 'section');
    expect(cards.some((c) => descendants(c).includes(setup[0]))).toBe(false);

    expect(blockFor('desktop', 'win-x64')).toEqual([]);
    expect(blockFor('tui', 'win-x64')).toEqual([]);

    const invented = hrefsIn(rendered.root).filter((href) => !rendered.publishedUrls.has(href));
    expect(invented).toEqual([]);
  });

  it('lists the setup once, and keeps the desktop-only setup and the raw binary', async () => {
    RELEASES = releaseAt(Object.values(SETUP_RELEASE_FILES));
    rendered = await render();
    const links = descendants(theList()).filter((e) => e.tag === 'a');
    const hrefs = links.map((a) => a.attrs.href as string);

    expect(hrefs.filter((h) => h === setupUrl('win-x64-setup'))).toHaveLength(1);
    expect(hrefs).toContain(setupDesktopUrl('win-x64'));
    expect(hrefs).toContain(setupUrl('win-x64'));

    const names = links.map((a) =>
      typeof a.attrs['aria-label'] === 'string' ? a.attrs['aria-label'] : textOf(a),
    );
    expect(new Set(names).size).toBe(names.length);
    expect(names.find((n) => n.includes('Desktop app only'))).toContain('Windows (x64)');
  });

  it('gives each Windows card its own file when the release carries no setup', async () => {
    const { 'win-x64-setup': _setup, ...rest } = SETUP_RELEASE_FILES;
    RELEASES = releaseAt(Object.values(rest));
    rendered = await render();

    expect(blockFor('setup', 'win-x64')).toEqual([]);
    const [tui] = blockFor('tui', 'win-x64');
    expect(tui.attrs['data-kind']).toBe('binary');
    expect(hrefsIn(tui)).toEqual([setupUrl('win-x64')]);
    expect(blockFor('desktop', 'win-x64').flatMap(hrefsIn)).toEqual([setupDesktopUrl('win-x64')]);
    expect(hrefsIn(rendered.root).filter((h) => h.includes('win-x64-setup.exe') && !h.includes('agent-ui'))).toEqual([]);
    // With no setup line to contrast with, the desktop file is not "only" anything.
    const listNames = descendants(theList()).filter((e) => e.tag === 'a').map(textOf);
    expect(listNames.filter((n) => n.includes('Desktop app only'))).toEqual([]);
    expect(listNames).toContain('Windows (x64) — Installer (.exe) · 94.2 MB');
  });
});

// ---- The card buttons, per machine -----------------------------------------
//
// The script reveals the blocks whose platform resolveTargets returns; these
// run the same resolution on fake navigators against v0.25.0 as GitHub carries
// it once the installers are attached.

const FULL_FILES = [
  ...Object.values(SETUP_DESKTOP_FILES),
  'gaia-agent-ui-0.25.0-amd64.deb',
  ...Object.values(SETUP_RELEASE_FILES),
  'gaia-0.25.0-darwin-x64.pkg',
  'gaia-0.25.0.x86_64.rpm',
];
const fullUrl = (name: string) => assetUrl(`v${SETUP_VERSION}`, name);

const UA = {
  win: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
  mac: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
  macSafari:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
  linux: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
  linuxArm: 'Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
  iphone: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148',
  android:
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36',
  chromeOs:
    'Mozilla/5.0 (X11; CrOS x86_64 15633.69.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36',
};

const fakeNav = (userAgent: string, architecture?: string) => ({
  userAgent,
  maxTouchPoints: 0,
  userAgentData:
    architecture === undefined
      ? undefined
      : { getHighEntropyValues: async () => ({ architecture }) },
});

/** The blocks the script reveals for this navigator. */
async function landing(navigator: ReturnType<typeof fakeNav>) {
  const { resolveTargets } = await import('../scripts/download-target');
  const targets: string[] = await resolveTargets(navigator);
  const shown = (surface: string) =>
    ctas().filter(
      (b) => b.attrs['data-surface'] === surface && targets.includes(String(b.attrs['data-platform'])),
    );
  return { desktop: shown('desktop'), tui: shown('tui'), setup: shown('setup') };
}

describe('Downloads.astro card buttons', () => {
  beforeEach(async () => {
    RELEASES = [stable(SETUP_VERSION, FULL_FILES)];
    rendered = await render();
  });

  it('shows Windows x64 only the one installer', async () => {
    const { desktop, tui, setup } = await landing(fakeNav(UA.win, 'x86'));
    expect(desktop).toEqual([]);
    expect(tui).toEqual([]);
    expect(setup.map(hrefsIn)).toEqual([[fullUrl('gaia-0.25.0-win-x64-setup.exe')]]);
  });

  it('gives an Apple Silicon Mac the .dmg and the terminal .pkg', async () => {
    const { desktop, tui, setup } = await landing(fakeNav(UA.mac, 'arm'));
    expect(desktop.map(hrefsIn)).toEqual([[fullUrl('gaia-agent-ui-0.25.0-arm64.dmg')]]);
    expect(tui.map(hrefsIn)).toEqual([[fullUrl('gaia-0.25.0-darwin-arm64.pkg')]]);
    expect(setup).toEqual([]);
  });

  it('gives Linux x64 the AppImage and the terminal .deb, the rest folded', async () => {
    const { desktop, tui } = await landing(fakeNav(UA.linux, 'x86'));
    expect(desktop.map(hrefsIn)).toEqual([[fullUrl('gaia-agent-ui-0.25.0-x86_64.AppImage')]]);
    expect(tui.map(hrefsIn)).toEqual([[fullUrl('gaia_0.25.0_amd64.deb')]]);
    expect(hrefsIn(theList())).toEqual(
      expect.arrayContaining([fullUrl('gaia-agent-ui-0.25.0-amd64.deb'), fullUrl('gaia-0.25.0.x86_64.rpm')]),
    );
  });

  it('tells the no-build platforms there is no desktop app, and offers the terminal', async () => {
    const cases = [
      [fakeNav(UA.mac, 'x86'), 'macOS (Intel)', 'gaia-0.25.0-darwin-x64.pkg'],
      [fakeNav(UA.win, 'arm'), 'Windows (ARM64)', 'gaia-windows-arm64.exe'],
      [fakeNav(UA.linuxArm, 'arm'), 'Linux (ARM64)', 'gaia-linux-arm64'],
    ] as const;
    for (const [nav, platform, file] of cases) {
      const { desktop, tui } = await landing(nav);
      expect(desktop).toHaveLength(1);
      expect(desktop[0].attrs['data-kind']).toBe('none');
      expect(hrefsIn(desktop[0])).toEqual([]);
      expect(textOf(desktop[0])).toContain(`No desktop app for ${platform}`);
      expect(tui.map(hrefsIn)).toEqual([[fullUrl(file)]]);
    }
  });

  it('shows a Mac of unknown architecture both Macs, Apple Silicon first', async () => {
    const { desktop, tui } = await landing(fakeNav(UA.macSafari));
    expect(desktop.map((b) => b.attrs['data-kind'])).toEqual(['build', 'none']);
    expect(desktop.map(hrefsIn)).toEqual([[fullUrl('gaia-agent-ui-0.25.0-arm64.dmg')], []]);
    expect(tui.map(hrefsIn)).toEqual([
      [fullUrl('gaia-0.25.0-darwin-arm64.pkg')],
      [fullUrl('gaia-0.25.0-darwin-x64.pkg')],
    ]);
  });
});

// ---- What the release does not carry ---------------------------------------

describe('Downloads.astro against an incomplete release', () => {
  // v0.25.0 as GitHub has it: the desktop app and the raw terminal binaries,
  // and installers for macOS and Linux but no combined Windows setup. Each
  // missing installer is absent — never a link built from a name the release
  // does not carry.
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
      'win-x64': ['binary', setupUrl('win-x64')],
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
    expect(hrefs.filter((h) => /\.(pkg|deb|rpm)$|gaia-0\.25\.0-win-x64-setup/.test(h))).toEqual([]);
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

// ---- The header's Install button -------------------------------------------
//
// Install starts the same file the install section leads with: the combined
// setup on Windows x64, the desktop app on Apple Silicon and Linux x64. The
// script swaps the href in once pickInstallTarget places the machine on exactly
// one platform; these read the per-platform targets the header ships and run
// the same picker the script does.

async function renderHeader(): Promise<El> {
  vi.resetModules();
  const { experimental_AstroContainer } = await import('astro/container');
  const Header = (await import('./Header.astro')).default;
  const container = await experimental_AstroContainer.create();
  return parse(await container.renderToString(Header));
}

const installLinks = (header: El) =>
  descendants(header).filter((e) => 'data-install-link' in e.attrs);

// The minimal reader leaves entities as written; the browser decodes them.
const decode = (v: string) =>
  v.replace(/&(#34|quot);/g, '"').replace(/&(#39|apos);/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
const targetsOf = (link: El): Record<string, { href: string; label: string }> =>
  JSON.parse(decode(String(link.attrs['data-install-targets'])));

/** Where Install takes this visitor: a file URL, or the section. */
async function installHref(targets: Record<string, { href: string }>, nav: ReturnType<typeof fakeNav>) {
  const { pickInstallTarget, resolveTargets } = await import('../scripts/download-target');
  return pickInstallTarget<{ href: string }>(await resolveTargets(nav), targets)?.href ?? '/#install';
}

describe('Header.astro Install button', () => {
  beforeEach(() => {
    RELEASES = [stable(SETUP_VERSION, FULL_FILES)];
  });

  it('starts the same file the install section leads with, on every platform it has', async () => {
    rendered = await render();
    const links = installLinks(await renderHeader());
    // The desktop nav and the mobile menu.
    expect(links).toHaveLength(2);

    for (const link of links) {
      // No JS, or a machine the script cannot place, lands on the section.
      expect(link.attrs.href).toBe('/#install');
      const targets = targetsOf(link);
      expect(Object.keys(targets).sort()).toEqual(['darwin-arm64', 'linux-x64', 'win-x64']);
      expect(blockFor('setup', 'win-x64').flatMap(hrefsIn)).toEqual([targets['win-x64'].href]);
      expect(blockFor('desktop', 'darwin-arm64').flatMap(hrefsIn)).toEqual([targets['darwin-arm64'].href]);
      expect(blockFor('desktop', 'linux-x64').flatMap(hrefsIn)).toEqual([targets['linux-x64'].href]);
      expect(targets['win-x64'].label).toBe(
        'Download the GAIA installer for Windows (x64): desktop app, terminal, or both (gaia-0.25.0-win-x64-setup.exe)',
      );
    }
  });

  it('starts the combined setup on Windows x64, never the desktop-only setup', async () => {
    const targets = targetsOf(installLinks(await renderHeader())[0]);
    const setup = fullUrl('gaia-0.25.0-win-x64-setup.exe');
    expect(await installHref(targets, fakeNav(UA.win, 'x86'))).toBe(setup);
    // A browser with no client hints: the UA's Win64 places it.
    expect(await installHref(targets, fakeNav(UA.win))).toBe(setup);
    expect(Object.values(targets).map((t) => t.href)).not.toContain(fullUrl('gaia-agent-ui-0.25.0-x64-setup.exe'));
  });

  it('starts the desktop app on Apple Silicon and Linux x64', async () => {
    const targets = targetsOf(installLinks(await renderHeader())[0]);
    expect(await installHref(targets, fakeNav(UA.mac, 'arm'))).toBe(fullUrl('gaia-agent-ui-0.25.0-arm64.dmg'));
    expect(await installHref(targets, fakeNav(UA.linux, 'x86'))).toBe(
      fullUrl('gaia-agent-ui-0.25.0-x86_64.AppImage'),
    );
  });

  it('keeps the section for every other machine, or one it cannot place', async () => {
    const targets = targetsOf(installLinks(await renderHeader())[0]);
    const cases: [string, ReturnType<typeof fakeNav>][] = [
      ['Intel Mac', fakeNav(UA.mac, 'x86')],
      ['Windows on ARM', fakeNav(UA.win, 'arm')],
      ['Linux ARM64 (hints)', fakeNav(UA.linuxArm, 'arm')],
      ['Linux ARM64 (UA only)', fakeNav(UA.linuxArm)],
      ['Mac of unknown architecture', fakeNav(UA.macSafari)],
      ['iPhone', fakeNav(UA.iphone)],
      ['Android', fakeNav(UA.android, 'arm')],
      ['ChromeOS', fakeNav(UA.chromeOs, 'x86')],
      ['empty UA', fakeNav('')],
    ];
    for (const [name, nav] of cases) {
      expect([name, await installHref(targets, nav)]).toEqual([name, '/#install']);
    }
  });

  it('keeps Windows on the section when the release carries no combined setup', async () => {
    RELEASES = [stable(SETUP_VERSION, FULL_FILES.filter((f) => f !== 'gaia-0.25.0-win-x64-setup.exe'))];
    const targets = targetsOf(installLinks(await renderHeader())[0]);
    expect(Object.keys(targets)).toEqual(['darwin-arm64', 'linux-x64']);
    // Not the desktop-only setup or the terminal binary promoted in its place.
    expect(await installHref(targets, fakeNav(UA.win, 'x86'))).toBe('/#install');
    expect(await installHref(targets, fakeNav(UA.mac, 'arm'))).toBe(fullUrl('gaia-agent-ui-0.25.0-arm64.dmg'));
  });

  it('keeps Windows on the section for a terminal-only setup from before 0.25.0', async () => {
    RELEASES = defaultReleases();
    const targets = targetsOf(installLinks(await renderHeader())[0]);
    expect(targets['win-x64']).toBeUndefined();
    expect(await installHref(targets, fakeNav(UA.win, 'x86'))).toBe('/#install');
  });

  it('fails the build when the release carries nothing Install can download', async () => {
    const { 'win-x64-setup': _setup, ...rest } = SETUP_RELEASE_FILES;
    RELEASES = [stable(SETUP_VERSION, Object.values(rest))];
    await expect(renderHeader()).rejects.toThrow(
      /\[install\] v0\.25\.0 carries \d+ assets, none of which the header's Install button can download/,
    );
  });
});
