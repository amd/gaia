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
// step, lets the hub manifest be a fixture instead of the live network, and
// fails at the component rather than at whichever page happened to embed it.

import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

const HUB = 'https://hub.test';
const VERSION = '0.24.1';

// Filenames are spelled out rather than produced by artifactFileName: a test
// that asks the source for the name it expects cannot notice the source
// changing it. These are what hub.amd-gaia.ai publishes today.
//
// agent-ui's paths deliberately do NOT follow `agents/<id>/<version>/<file>`,
// so an href that re-derives the object key instead of honouring the published
// `path` fails here rather than in a visitor's 404.
const DESKTOP_FILES = {
  'win-x64': 'gaia-agent-ui-0.24.1-x64-setup.exe',
  'darwin-arm64': 'gaia-agent-ui-0.24.1-arm64.dmg',
  'linux-x64': 'gaia-agent-ui-0.24.1-x86_64.AppImage',
} as const;

const desktopUrl = (platform: keyof typeof DESKTOP_FILES) =>
  `${HUB}/dl/desktop/${DESKTOP_FILES[platform]}`;

const TUI_FILES = {
  'win-x64': 'gaia-win-x64.exe',
  'win-arm64': 'gaia-win-arm64.exe',
  'darwin-x64': 'gaia-darwin-x64',
  'darwin-arm64': 'gaia-darwin-arm64',
  'linux-x64': 'gaia-linux-x64',
  'linux-arm64': 'gaia-linux-arm64',
  'win-x64-setup': 'gaia-0.24.1-win-x64-setup.exe',
  'darwin-arm64-pkg': 'gaia-0.24.1-darwin-arm64.pkg',
  'darwin-x64-pkg': 'gaia-0.24.1-darwin-x64.pkg',
  'linux-x64-deb': 'gaia_0.24.1_amd64.deb',
  'linux-x64-rpm': 'gaia-0.24.1.x86_64.rpm',
} as const;

const tuiUrl = (key: keyof typeof TUI_FILES) =>
  `${HUB}/agents/terminal-hub/${VERSION}/${TUI_FILES[key]}`;

const artifact = (filename: string, path: string) => ({
  filename,
  path,
  size_bytes: 98_765_432,
  sha256: 'a'.repeat(64),
  content_type: 'application/octet-stream',
});

const MANIFESTS: Record<string, unknown> = {
  'agent-ui': {
    latest_version: VERSION,
    versions: {
      [VERSION]: {
        artifacts: [
          ...Object.values(DESKTOP_FILES).map((f) => artifact(f, `dl/desktop/${f}`)),
          // Published beside the builds and matched by no key — the card has to
          // ignore it rather than offer a sidecar file as a download.
          artifact(
            'gaia-agent-ui-0.24.1-arm64.dmg.blockmap',
            'dl/desktop/gaia-agent-ui-0.24.1-arm64.dmg.blockmap',
          ),
        ],
      },
    },
  },
  'terminal-hub': {
    latest_version: VERSION,
    versions: {
      [VERSION]: {
        artifacts: Object.values(TUI_FILES).map((f) =>
          artifact(f, `agents/terminal-hub/${VERSION}/${f}`),
        ),
      },
    },
  },
};

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

async function render() {
  vi.resetModules();
  process.env.HUB_CATALOG_URL = HUB;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string | URL) => {
      const url = String(input);
      const id = /\/agents\/([^/]+)\/manifest\.json/.exec(url)?.[1];
      const manifest = id ? MANIFESTS[id] : undefined;
      if (!manifest) throw new Error(`unexpected fetch in test: ${url}`);
      return { ok: true, status: 200, json: async () => manifest } as Response;
    }),
  );

  const { getComponentRelease } = await import('../data/catalog');
  const { experimental_AstroContainer } = await import('astro/container');
  const Downloads = (await import('./Downloads.astro')).default;

  const container = await experimental_AstroContainer.create();
  const html = await container.renderToString(Downloads);
  const [desktop, tui] = await Promise.all([
    getComponentRelease('agent-ui'),
    getComponentRelease('terminal-hub'),
  ]);

  return {
    html,
    root: parse(html),
    publishedUrls: new Set([...desktop.binaries, ...tui.binaries].map((b) => b.url)),
  };
}

let rendered: Awaited<ReturnType<typeof render>>;

beforeEach(async () => {
  rendered = await render();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.HUB_CATALOG_URL;
});

const ctas = () => descendants(rendered.root).filter((e) => 'data-dl-cta' in e.attrs);

describe('Downloads.astro', () => {
  it('links only to files the hub actually published', () => {
    const links = descendants(rendered.root).filter(
      (e) => e.tag === 'a' && typeof e.attrs.href === 'string',
    );
    const invented = links
      .map((a) => a.attrs.href as string)
      .filter((href) => !rendered.publishedUrls.has(href));
    expect(invented).toEqual([]);

    // 3 desktop CTAs + 7 terminal CTAs + 3 + 11 in the all-downloads list.
    expect(links).toHaveLength(24);
    expect(rendered.publishedUrls.size).toBe(15);
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

    // 6 desktop blocks (3 builds, 3 "no build"), 6 terminal rows, 2 captions.
    expect(reveals.length).toBe(14);
    expect(hidden.sort()).toEqual([...reveals].sort());

    // The full list is the no-JS offer, so it must render open.
    const list = all.filter((e) => 'data-dl-list' in e.attrs);
    expect(list.length).toBe(1);
    expect('hidden' in list[0].attrs).toBe(false);
  });

  it('offers each block only its own platform, and no build where there is none', () => {
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

  it('names the platform in every link of the all-downloads list', () => {
    const [list] = descendants(rendered.root).filter((e) => 'data-dl-list' in e.attrs);
    const names = descendants(list)
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
    for (const block of ctas()) {
      expect(String(block.parent?.attrs.class ?? '')).toMatch(/\bspace-y-\d/);
    }
  });

  it('keeps the two cards under the region heading, not peer to the page', () => {
    const all = descendants(rendered.root);
    const h2s = all.filter((e) => e.tag === 'h2');
    expect(h2s).toHaveLength(1);
    expect(String(h2s[0].attrs.class ?? '')).toContain('sr-only');
    expect(all.filter((e) => e.tag === 'h3').map(textOf)).toEqual(['Desktop app', 'Terminal']);
  });
});
