// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Contrast gate for tokens.css — the website's half of the floors in
// docs/spec/gaia-design-language.mdx. The TUI enforces the same numbers in
// tui/internal/ui/theme/contrast_test.go; this file follows its discipline:
// every colour token must declare what it owes the reader, and a token added
// without a role fails the suite rather than shipping unmeasured.
//
// It parses tokens.css rather than re-listing the palette, so a hex edited in
// the stylesheet is the thing under test — a copy here could drift and pass.

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

// --------------------------------------------------------------------- colour

type RGB = [number, number, number];
/** A declared value: colour plus the alpha it is painted at (1 for triplets). */
type Paint = { rgb: RGB; alpha: number };

const channel = (c: number): number => {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};

/** WCAG 2.1 relative luminance. */
const luminance = ([r, g, b]: RGB): number =>
  0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);

/** WCAG 2.1 contrast ratio, 1..21. */
function contrast(a: RGB, b: RGB): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Composite a translucent paint over an opaque ground. */
const composite = (fg: Paint, bg: RGB): RGB =>
  fg.rgb.map((c, i) => Math.round(c * fg.alpha + bg[i] * (1 - fg.alpha))) as RGB;

const hex = (c: RGB): string => '#' + c.map((x) => x.toString(16).padStart(2, '0')).join('');

// ---------------------------------------------------------------------- parse

const TRIPLET = /^(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})$/;
const RGBA = /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([\d.]+)\s*)?\)$/;

function asPaint(value: string): Paint | null {
  const t = TRIPLET.exec(value);
  if (t) return { rgb: [+t[1], +t[2], +t[3]], alpha: 1 };
  const r = RGBA.exec(value);
  if (r) return { rgb: [+r[1], +r[2], +r[3]], alpha: r[4] === undefined ? 1 : +r[4] };
  return null; // shadows, scalars — not a colour
}

/**
 * Read the `:root` and `[data-theme='dark']` declaration blocks out of
 * tokens.css. Neither contains a nested rule, so `[^{}]*` is enough to bound
 * them and keeps @keyframes / @media out of the result.
 */
function readThemes(): { light: Map<string, string>; dark: Map<string, string> } {
  const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, ''); // hexes live in the comments too
  const light = new Map<string, string>();
  const darkOnly = new Map<string, string>();

  const blocks = css.matchAll(/(:root|\[data-theme='dark'\])\s*\{([^{}]*)\}/g);
  let sawRoot = false;
  let sawDark = false;
  for (const [, selector, body] of blocks) {
    const target = selector === ':root' ? light : darkOnly;
    if (selector === ':root') sawRoot = true;
    else sawDark = true;
    for (const decl of body.split(';')) {
      const m = /^\s*--([\w-]+)\s*:\s*([\s\S]+?)\s*$/.exec(decl);
      if (m) target.set(`--${m[1]}`, m[2].replace(/\s+/g, ' ').trim());
    }
  }
  // A silent parse failure would turn every assertion below into a no-op.
  if (!sawRoot || !sawDark) throw new Error('tokens.css: could not find both theme blocks');

  return { light, dark: new Map([...light, ...darkOnly]) };
}

const { light, dark } = readThemes();
/** Tokens the dark block overrides; the rest are deliberately mode-invariant. */
const themed = new Set(
  [...dark].filter(([k, v]) => light.get(k) !== v).map(([k]) => k),
);

// ----------------------------------------------------------------- the floors

const FLOOR = {
  /** Carries information as words. WCAG 2.1 AA body text. */
  text: 4.5,
  /** Deliberately recessive; always repeats something on the same row. */
  recessive: 3.0,
  /** A rule, a bar track, a fill — carries nothing, must only stay visible. */
  chrome: 1.5,
} as const;

type Role = keyof typeof FLOOR | 'ground' | 'pair-only';

/**
 * Every colour token in tokens.css and the job it does. `page` tokens are
 * measured against the page grounds, `code` tokens against --g-code-bg (the
 * code panel is dark in both themes, so its palette is pinned and judged only
 * against itself). A token missing from this table fails
 * "every colour token declares a floor" — which is the point.
 */
const ROLES: Record<string, { role: Role; on: 'page' | 'code' }> = {
  // Grounds. Everything else is measured against these.
  '--g-bg': { role: 'ground', on: 'page' },
  '--g-bg2': { role: 'ground', on: 'page' },
  '--g-surface': { role: 'ground', on: 'page' },
  '--g-surface2': { role: 'ground', on: 'page' },
  '--g-hdr': { role: 'ground', on: 'page' },
  '--g-code-bg': { role: 'ground', on: 'code' },

  // Type.
  '--g-text': { role: 'text', on: 'page' },
  '--g-muted': { role: 'text', on: 'page' },
  '--g-faint': { role: 'recessive', on: 'page' },

  // Accent. The accent is a text colour, so it carries the text floor even
  // where it is only painted as a dot or a bar.
  '--g-accent': { role: 'text', on: 'page' },
  '--g-accent2': { role: 'text', on: 'page' },
  '--g-accent-text': { role: 'text', on: 'page' },
  '--g-focus': { role: 'text', on: 'page' },
  '--g-accent-dim': { role: 'chrome', on: 'page' },

  // Hairlines.
  '--g-border': { role: 'chrome', on: 'page' },
  '--g-border2': { role: 'chrome', on: 'page' },

  // Fills cover the ground, so against it they only have to read as a block;
  // legibility is the pair's job — see "filled buttons carry their text".
  '--g-accent-fill': { role: 'chrome', on: 'page' },
  '--g-accent-fill2': { role: 'chrome', on: 'page' },
  '--g-on-accent': { role: 'pair-only', on: 'page' },

  // Code panel.
  '--g-code-text': { role: 'text', on: 'code' },
  '--g-code-faint': { role: 'text', on: 'code' },
  '--g-code-accent': { role: 'text', on: 'code' },
  '--g-code-amber': { role: 'text', on: 'code' },
  // Semantic hues. Each one spells a word (`ok`, `urgent`, a string literal),
  // so each carries the text floor rather than the recessive one.
  '--g-code-green': { role: 'text', on: 'code' },
  '--g-code-blue': { role: 'text', on: 'code' },
  '--g-code-danger': { role: 'text', on: 'code' },
  '--g-code-purple': { role: 'text', on: 'code' },
  '--g-code-cyan': { role: 'text', on: 'code' },
};

/** Not colours: shadow stacks. Listed so the completeness check stays honest. */
const NON_COLOUR = new Set(['--g-shadow-card', '--g-shadow-btn', '--g-shadow-terminal']);

// ---------------------------------------------------------------- the grounds

/**
 * Every opaque surface a page token can land on: the canvas, the raised band,
 * and each translucent surface composited over both. Measuring only the flat
 * canvas is how a value that is fine on the page goes marginal inside a card.
 */
function pageGrounds(vars: Map<string, string>): Array<{ name: string; rgb: RGB }> {
  const paint = (n: string): Paint => {
    const p = asPaint(vars.get(n)!);
    if (!p) throw new Error(`${n} is not a colour`);
    return p;
  };
  const bg = paint('--g-bg').rgb;
  const bg2 = paint('--g-bg2').rgb;
  const out = [
    { name: 'canvas', rgb: bg },
    { name: 'band', rgb: bg2 },
  ];
  for (const layer of ['--g-surface', '--g-surface2', '--g-hdr']) {
    const p = paint(layer);
    out.push({ name: `${layer} over canvas`, rgb: composite(p, bg) });
    out.push({ name: `${layer} over band`, rgb: composite(p, bg2) });
  }
  return out;
}

function groundsFor(kind: 'page' | 'code', vars: Map<string, string>) {
  if (kind === 'code') return [{ name: 'code panel', rgb: asPaint(vars.get('--g-code-bg')!)!.rgb }];
  return pageGrounds(vars);
}

const THEMES: Array<[string, Map<string, string>]> = [
  ['light', light],
  ['dark', dark],
];

// ----------------------------------------------------------------- the checks

describe('tokens.css parses', () => {
  it('finds both themes and the full token set', () => {
    expect(light.get('--g-bg')).toBe('245 242 236');
    expect(dark.get('--g-bg')).toBe('23 22 28');
    // Mode-invariant tokens must be inherited by the dark theme, not dropped.
    expect(dark.get('--g-accent-fill')).toBe(light.get('--g-accent-fill'));
  });

  it('every colour token declares a floor', () => {
    const undeclared = [...light.keys()].filter(
      (k) => !NON_COLOUR.has(k) && !(k in ROLES) && asPaint(light.get(k)!) !== null,
    );
    expect(undeclared, 'add these to ROLES with the contrast they owe').toEqual([]);
  });

  it('declares no floor for a token that no longer exists', () => {
    const stale = Object.keys(ROLES).filter((k) => !light.has(k));
    expect(stale).toEqual([]);
  });

  it('classifies the shadow stacks as non-colours, not as missing roles', () => {
    for (const k of NON_COLOUR) {
      expect(light.has(k), k).toBe(true);
      expect(asPaint(light.get(k)!), k).toBeNull();
    }
  });
});

describe.each(THEMES)('%s theme holds its floors', (themeName, vars) => {
  const entries = Object.entries(ROLES).filter(
    ([, r]) => r.role !== 'ground' && r.role !== 'pair-only',
  );

  it.each(entries)('%s', (token, { role, on }) => {
    const floor = FLOOR[role as keyof typeof FLOOR];
    const paint = asPaint(vars.get(token)!)!;
    for (const ground of groundsFor(on, vars)) {
      // A translucent token (a hairline) is judged as it is actually painted.
      const actual = composite(paint, ground.rgb);
      const ratio = contrast(actual, ground.rgb);
      expect(
        Number(ratio.toFixed(2)),
        `${themeName} ${token} ${hex(actual)} on ${ground.name} ${hex(ground.rgb)} — ` +
          `${ratio.toFixed(2)}:1, floor ${floor}:1 (${role})`,
      ).toBeGreaterThanOrEqual(floor);
    }
  });
});

describe.each(THEMES)('%s theme: filled buttons carry their text', (themeName, vars) => {
  // The pair covers the ground, so it is judged against itself.
  it.each(['--g-accent-fill', '--g-accent-fill2'])('white on %s', (fillToken) => {
    const fill = asPaint(vars.get(fillToken)!)!.rgb;
    const on = asPaint(vars.get('--g-on-accent')!)!.rgb;
    const ratio = contrast(on, fill);
    expect(
      Number(ratio.toFixed(2)),
      `${themeName} ${hex(on)} on ${hex(fill)} — ${ratio.toFixed(2)}:1, floor ${FLOOR.text}:1`,
    ).toBeGreaterThanOrEqual(FLOOR.text);
  });

  it('uses the same fill in both themes, so one on-colour serves both', () => {
    expect(themed.has('--g-accent-fill')).toBe(false);
    expect(themed.has('--g-accent-fill2')).toBe(false);
    expect(themed.has('--g-on-accent')).toBe(false);
  });
});

describe('the palette is the one the design language pins', () => {
  // docs/spec/gaia-design-language.mdx — graphite / ivory / copper. These are
  // shared verbatim with the Agent UI and the TUI, so a "small tweak" here
  // silently desynchronises three surfaces.
  const PINNED: Array<[string, string, string]> = [
    // token, light, dark
    ['--g-bg', '#f5f2ec', '#17161c'],
    ['--g-bg2', '#eae5dc', '#222128'],
    ['--g-text', '#242129', '#f0ede7'],
    ['--g-muted', '#645d6a', '#aaa5b0'],
    ['--g-accent', '#9a4930', '#eba474'],
    ['--g-focus', '#9a4930', '#eba474'],
  ];

  it.each(PINNED)('%s', (token, expectLight, expectDark) => {
    expect(hex(asPaint(light.get(token)!)!.rgb)).toBe(expectLight);
    expect(hex(asPaint(dark.get(token)!)!.rgb)).toBe(expectDark);
  });

  it('fills with burnished copper in both themes, carrying white', () => {
    expect(hex(asPaint(light.get('--g-accent-fill')!)!.rgb)).toBe('#9a4930');
    expect(hex(asPaint(light.get('--g-on-accent')!)!.rgb)).toBe('#ffffff');
    expect(contrast([255, 255, 255], [0x9a, 0x49, 0x30])).toBeCloseTo(6.23, 1);
  });

  // The browser-chrome colour is the one canvas value that cannot come from a
  // CSS custom property — a <meta> tag can't read one — so Layout.astro and
  // ThemeToggle.astro repeat both canvas hexes in JS. Pin them here: an edit to
  // --g-bg that misses these leaves the phone status bar on the old palette,
  // which no page-level assertion would catch.
  it.each(['../layouts/Layout.astro', '../components/ThemeToggle.astro'])(
    'keeps the theme-color literals in %s equal to the canvas tokens',
    (file) => {
      const src = readFileSync(new URL(file, import.meta.url), 'utf8');
      const found = new Set(
        [...src.matchAll(/#[0-9a-fA-F]{6}\b/g)].map((m) => m[0].toLowerCase()),
      );
      const canvases = [light, dark].map((v) => hex(asPaint(v.get('--g-bg')!)!.rgb));
      expect([...found].sort()).toEqual([...new Set(canvases)].sort());
    },
  );

  it('keeps no gold-family accent behind', () => {
    // #E7A33C and its neighbours were the previous brand colour. Copper's hue
    // is ~15°; the old gold sat at ~36°. Anything back in that arc means a
    // stray value survived the retheme.
    for (const [name, vars] of THEMES) {
      for (const token of ['--g-accent', '--g-accent2', '--g-accent-text', '--g-accent-fill']) {
        const [r, g, b] = asPaint(vars.get(token)!)!.rgb;
        const max = Math.max(r, g, b);
        const min = Math.min(r, g, b);
        if (max === min) continue;
        const hue =
          60 *
          (((max === r ? (g - b) / (max - min) : max === g ? 2 + (b - r) / (max - min) : 4 + (r - g) / (max - min)) %
            6) +
            6) %
          360;
        expect(hue, `${name} ${token} hue ${hue.toFixed(1)}° is back in the gold arc`).toBeLessThan(
          28,
        );
      }
    }
  });
});
