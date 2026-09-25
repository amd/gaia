// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The website, the Agent UI and the TUI are claimed to paint the same copper
// and the same status hues. Nothing enforced that claim, so the two ambers
// drifted apart (#e3b341 here vs #E5B567 there) while every contrast test on
// both sides stayed green — each value clears its floor, they just are not the
// same colour.
//
// This reads the other two surfaces' token files from the repo and compares the
// literals directly, because "same assistant" is a claim about equality, not
// about contrast.

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

/** Agent UI token home, relative to this file: website/src/design → repo root. */
const AGENT_UI_TOKENS = fileURLToPath(
  new URL('../../../src/gaia/apps/webui/src/styles/index.css', import.meta.url),
);

const WEBSITE_TOKENS = fileURLToPath(new URL('./tokens.css', import.meta.url));

/** TUI token home. Go, not CSS — the literals are the thing that has to match. */
const TUI_THEME = fileURLToPath(
  new URL('../../../tui/internal/ui/theme/theme.go', import.meta.url),
);

const WEBSITE_CI = fileURLToPath(
  new URL('../../../.github/workflows/website-ci.yml', import.meta.url),
);

// resolve() drops the trailing separator, so these compare as path prefixes.
const REPO_ROOT = resolve(fileURLToPath(new URL('../../../', import.meta.url)));
const WEBSITE_ROOT = resolve(fileURLToPath(new URL('../../', import.meta.url)));
const WEBSITE_SRC = resolve(fileURLToPath(new URL('../', import.meta.url)));

/**
 * Website role → the Agent UI role it must equal.
 *
 * The status hues inside the code panel, plus the copper the primary button is
 * painted with. The syntax-only colours (purple, cyan) have no Agent UI
 * counterpart by design, and the surface/text roles differ on purpose: the
 * panels are near-black but not the same near-black.
 */
const SHARED: Record<string, string> = {
  '--g-code-accent': '--code-accent',
  '--g-code-green': '--code-success',
  '--g-code-blue': '--code-info',
  '--g-code-amber': '--code-warning',
  '--g-code-danger': '--code-danger',
  // The primary button. Its resting fill, its hover step and the text on top
  // are the same three literals on both surfaces — a reader who clicks Download
  // on the site and Send in the app is looking at one control, not two.
  '--g-accent': '--accent',
  '--g-accent-fill': '--accent-fill',
  '--g-accent-fill2': '--accent-fill-hover',
  '--g-on-accent': '--accent-fill-text',
};

/**
 * Every declaration of `token`, in file order, deduped — `#aabbcc` whichever
 * syntax it was written in (`12 34 56` triplet here, `#AABBCC` there).
 *
 * Reading *all* of them matters because a themed role is declared twice, once
 * per `:root` block. Matching only the first would compare the light values and
 * let a dark-theme drift through, which is the whole class of bug this file
 * exists to catch.
 */
function readValues(source: string, token: string): string[] {
  const pattern = new RegExp(
    `${token}\\s*:\\s*(?:(#[0-9a-fA-F]{6})|(\\d+)\\s+(\\d+)\\s+(\\d+))\\s*;`,
    'g',
  );
  const seen: string[] = [];
  for (const m of source.matchAll(pattern)) {
    const hex = m[1]
      ? m[1].toLowerCase()
      : `#${[m[2], m[3], m[4]].map((n) => Number(n).toString(16).padStart(2, '0')).join('')}`;
    if (!seen.includes(hex)) seen.push(hex);
  }
  return seen;
}

/**
 * Every `Name = lipgloss.AdaptiveColor{Light: …, Dark: …}` in theme.go, keyed by
 * token name. Keyed rather than indexed by line so a reorder, a new token above
 * it, or a reflowed comment cannot silently change which literal is under test.
 */
function readGoTokens(source: string): Map<string, string[]> {
  const pattern =
    /\b(\w+)\s*=\s*lipgloss\.AdaptiveColor\{\s*Light:\s*"(#[0-9a-fA-F]{6})"\s*,\s*Dark:\s*"(#[0-9a-fA-F]{6})"\s*,?\s*\}/g;
  const out = new Map<string, string[]>();
  for (const m of source.matchAll(pattern)) out.set(m[1], [m[2].toLowerCase(), m[3].toLowerCase()]);
  return out;
}

/** TUI token → the website and Agent UI roles carrying the same literal. */
const THREE_SURFACE: Array<[string, string, string]> = [
  ['Accent', '--g-accent', '--accent'],
  ['AccentFillBG', '--g-accent-fill', '--accent-fill'],
];

const website = readFileSync(WEBSITE_TOKENS, 'utf8');
const agentUi = readFileSync(AGENT_UI_TOKENS, 'utf8');
const tui = readGoTokens(readFileSync(TUI_THEME, 'utf8'));

describe('status hues are one set of literals across both surfaces', () => {
  it('can still find the Agent UI stylesheet', () => {
    // A rename upstream must fail here rather than silently skip every pair.
    expect(agentUi).toContain('--code-accent');
  });

  it.each(Object.entries(SHARED))('%s equals %s', (siteToken, appToken) => {
    const siteValues = readValues(website, siteToken);
    const appValues = readValues(agentUi, appToken);

    expect(siteValues, `${siteToken} is declared nowhere in tokens.css`).not.toEqual([]);
    expect(appValues, `${appToken} is declared nowhere in index.css`).not.toEqual([]);
    // Order is light-then-dark in both files, so this also catches a swap.
    expect(appValues).toEqual(siteValues);
  });
});

// The TUI is the third surface and the one that cannot import a stylesheet, so
// its copper is a hand-typed Go literal. A retune that lands on two surfaces and
// misses the terminal is invisible to every contrast test on all three.
describe('the copper is one literal on all three surfaces', () => {
  it('can still parse theme.go', () => {
    // A rename or a reshaped declaration must fail here, not skip every pair.
    expect([...tui.keys()]).toEqual(
      expect.arrayContaining(THREE_SURFACE.map(([goToken]) => goToken)),
    );
  });

  it.each(THREE_SURFACE)('%s equals %s equals %s', (goToken, siteToken, appToken) => {
    // A mode-invariant role is declared once on the web and twice in Go.
    const expected = [...new Set(tui.get(goToken)!)];
    expect(readValues(website, siteToken), `${siteToken} drifted from the TUI`).toEqual(expected);
    expect(readValues(agentUi, appToken), `${appToken} drifted from the TUI`).toEqual(expected);
  });

  it('is still burnished copper and its dark counterpart', () => {
    expect(tui.get('Accent')).toEqual(['#9a4930', '#eba474']);
    expect(tui.get('AccentFillBG')).toEqual(['#9a4930', '#9a4930']);
  });
});

// A drift guard that CI never runs is not a guard. This suite only executes in
// Website CI, which is path-filtered — so retuning a hue in the Agent UI alone,
// the exact change the pairs above exist to catch, skipped this file entirely.
// Every file the suite reads from outside website/ needs its own filter entry,
// so the check below finds them rather than naming one by hand.

/** Test and spec files under website/src, recursively. */
function suiteFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = resolve(dir, entry.name);
    if (entry.isDirectory()) out.push(...suiteFiles(full));
    else if (/\.(test|spec)\.ts$/.test(entry.name)) out.push(full);
  }
  return out;
}

/** Repo-relative, forward slashes — the spelling a workflow path filter uses. */
function crossTreeReads(): string[] {
  const reads = /new URL\(\s*['"]([^'"]+)['"]\s*,\s*import\.meta\.url\s*\)/g;
  const found = new Set<string>();
  for (const file of suiteFiles(WEBSITE_SRC))
    for (const [, spec] of readFileSync(file, 'utf8').matchAll(reads)) {
      const target = resolve(dirname(file), spec);
      if (target === WEBSITE_ROOT || target.startsWith(WEBSITE_ROOT + sep)) continue;
      // A directory is a path anchor, not a read — the reads resolve off it.
      if (!statSync(target).isFile()) continue;
      found.add(relative(REPO_ROOT, target).replace(/\\/g, '/'));
    }
  return [...found].sort();
}

/** GitHub's filter syntax: `*` stops at a separator, `**` crosses them. */
function matchesGlob(glob: string, path: string): boolean {
  const body = glob
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    .replace(/\*\*/g, '\u0000')
    .replace(/\*/g, '[^/]*')
    .replace(/\u0000/g, '.*');
  return new RegExp(`^${body}$`).test(path);
}

function pathFilters(block: string): string[] {
  const list = /^[ \t]*paths:[ \t]*\r?\n((?:[ \t]*-[ \t]*\S.*\r?\n)+)/m.exec(block);
  if (!list) return [];
  return [...list[1].matchAll(/^[ \t]*-[ \t]*['"]?(.+?)['"]?[ \t]*$/gm)].map((m) => m[1]);
}

describe('the drift guard runs on every tree it reads', () => {
  // Normalised: a CRLF checkout would otherwise leave \r inside every entry.
  const workflow = readFileSync(WEBSITE_CI, 'utf8').replace(/\r\n/g, '\n');
  const blocks = workflow.split(/^\s*(?:pull_request|push):\s*$/m).slice(1);
  const reads = crossTreeReads();

  it('still finds two trigger blocks, each with a path filter', () => {
    expect(blocks, 'website-ci.yml no longer has two trigger blocks').toHaveLength(2);
    for (const block of blocks)
      expect(pathFilters(block), 'a trigger block lost its paths: list').not.toEqual([]);
  });

  it('still finds the files the suite reads across trees', () => {
    // A scan that matches nothing would pass every case below vacuously.
    expect(reads, 'the cross-tree scan found nothing — did new URL(…) reads move?').not.toEqual([]);
    expect(reads, 'a new cross-tree read belongs here AND in website-ci.yml paths').toEqual([
      '.github/workflows/website-ci.yml',
      'docs/spec/gaia-design-language.mdx',
      'hub/agents/gaia/npm/package.json',
      'hub/agents/gaia/python/gaia-agent.yaml',
      'src/gaia/apps/webui/src/styles/index.css',
      'tui/internal/ui/theme/theme.go',
    ]);
  });

  it.each(reads)('%s is named by both path filters', (path) => {
    blocks.forEach((block, i) => {
      expect(
        pathFilters(block).some((glob) => matchesGlob(glob, path)),
        `add '${path}' to website-ci.yml's ${i === 0 ? 'pull_request' : 'push'} paths — ` +
          'a test reads it, so changing it has to run this suite',
      ).toBe(true);
    });
  });
});
