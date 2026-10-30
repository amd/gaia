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

const fromHex = (h: string): RGB =>
  [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB;

/** Hue in degrees, 0..360; null for a neutral. */
function hue([r, g, b]: RGB): number | null {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  if (max === min) return null;
  const d = max - min;
  const h = max === r ? (g - b) / d : max === g ? 2 + (b - r) / d : 4 + (r - g) / d;
  return ((h * 60) % 360 + 360) % 360;
}

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

const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g, // hexes live in the comments too
  '',
);

/**
 * Read the `:root` declaration block out of tokens.css. It contains no nested
 * rule, so `[^{}]*` is enough to bound it and keeps @keyframes / @media out.
 */
function readTokens(): Map<string, string> {
  const vars = new Map<string, string>();
  let sawRoot = false;
  for (const [, body] of css.matchAll(/:root\s*\{([^{}]*)\}/g)) {
    sawRoot = true;
    for (const decl of body.split(';')) {
      const m = /^\s*--([\w-]+)\s*:\s*([\s\S]+?)\s*$/.exec(decl);
      if (m) vars.set(`--${m[1]}`, m[2].replace(/\s+/g, ' ').trim());
    }
  }
  // A silent parse failure would turn every assertion below into a no-op.
  if (!sawRoot) throw new Error('tokens.css: could not find the :root block');
  return vars;
}

const vars = readTokens();
const paint = (token: string): Paint => {
  const p = asPaint(vars.get(token) ?? '');
  if (!p) throw new Error(`${token} is not a colour in tokens.css`);
  return p;
};

// ----------------------------------------------------------------- the floors

const FLOOR = {
  /** Carries information as words. WCAG 2.1 AA body text. */
  text: 4.5,
  /** Deliberately recessive; always repeats something on the same row. */
  recessive: 3.0,
  /** Display-size type and graphics only. WCAG 2.1 AA large text. */
  large: 3.0,
  /** A rule, a bar track, a fill — carries nothing, must only stay visible. */
  chrome: 1.5,
} as const;

type Role = keyof typeof FLOOR | 'ground' | 'pair-only';

/**
 * Every colour token in tokens.css and the job it does. `page` tokens are
 * measured against the page grounds, `code` tokens against --g-code-bg. A
 * token missing from this table fails "every colour token declares a floor" —
 * which is the point.
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

  // Accent. The brand red is display-size only; the lifted red is a text
  // colour, so it carries the text floor even where it is only a dot or a bar.
  '--g-brand': { role: 'large', on: 'page' },
  '--g-accent': { role: 'text', on: 'page' },
  '--g-accent2': { role: 'text', on: 'page' },
  '--g-accent-text': { role: 'text', on: 'page' },
  '--g-focus': { role: 'text', on: 'page' },
  '--g-accent-dim': { role: 'chrome', on: 'page' },

  // Release candidates.
  '--g-rc': { role: 'text', on: 'page' },
  '--g-rc-dim': { role: 'chrome', on: 'page' },

  // The starfield is drawn behind everything and carries nothing.
  '--g-sky-o': { role: 'chrome', on: 'page' },
  '--g-sky-b': { role: 'chrome', on: 'page' },
  '--g-sky-star': { role: 'chrome', on: 'page' },
  '--g-sky-f': { role: 'chrome', on: 'page' },
  '--g-sky-g': { role: 'chrome', on: 'page' },
  '--g-sky-k': { role: 'chrome', on: 'page' },
  '--g-sky-m': { role: 'chrome', on: 'page' },
  '--g-sky-hot': { role: 'chrome', on: 'page' },

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
function pageGrounds(): Array<{ name: string; rgb: RGB }> {
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

const groundsFor = (kind: 'page' | 'code') =>
  kind === 'code' ? [{ name: 'code panel', rgb: paint('--g-code-bg').rgb }] : pageGrounds();

// ----------------------------------------------------------------- the checks

describe('tokens.css parses', () => {
  it('finds the token set', () => {
    expect(vars.get('--g-bg')).toBe('0 0 0');
    expect(vars.size).toBeGreaterThan(30);
  });

  // The site is dark-only. A theme block coming back would carry values this
  // suite never measures.
  it('declares no second theme', () => {
    expect(css).not.toMatch(/\[data-theme/);
  });

  it('every colour token declares a floor', () => {
    const undeclared = [...vars.keys()].filter(
      (k) => !NON_COLOUR.has(k) && !(k in ROLES) && asPaint(vars.get(k)!) !== null,
    );
    expect(undeclared, 'add these to ROLES with the contrast they owe').toEqual([]);
  });

  it('declares no floor for a token that no longer exists', () => {
    expect(Object.keys(ROLES).filter((k) => !vars.has(k))).toEqual([]);
  });

  it('classifies the shadow stacks as non-colours, not as missing roles', () => {
    for (const k of NON_COLOUR) {
      expect(vars.has(k), k).toBe(true);
      expect(asPaint(vars.get(k)!), k).toBeNull();
    }
  });
});

describe('every token holds its floor', () => {
  const entries = Object.entries(ROLES).filter(
    ([, r]) => r.role !== 'ground' && r.role !== 'pair-only',
  );

  it.each(entries)('%s', (token, { role, on }) => {
    const floor = FLOOR[role as keyof typeof FLOOR];
    const p = paint(token);
    for (const ground of groundsFor(on)) {
      // A translucent token (a hairline) is judged as it is actually painted.
      const actual = composite(p, ground.rgb);
      const ratio = contrast(actual, ground.rgb);
      expect(
        Number(ratio.toFixed(2)),
        `${token} ${hex(actual)} on ${ground.name} ${hex(ground.rgb)} — ` +
          `${ratio.toFixed(2)}:1, floor ${floor}:1 (${role})`,
      ).toBeGreaterThanOrEqual(floor);
    }
  });
});

describe('filled buttons carry their text', () => {
  // The pair covers the ground, so it is judged against itself.
  it.each(['--g-accent-fill', '--g-accent-fill2'])('white on %s', (fillToken) => {
    const fill = paint(fillToken).rgb;
    const on = paint('--g-on-accent').rgb;
    const ratio = contrast(on, fill);
    expect(
      Number(ratio.toFixed(2)),
      `${hex(on)} on ${hex(fill)} — ${ratio.toFixed(2)}:1, floor ${FLOOR.text}:1`,
    ).toBeGreaterThanOrEqual(FLOOR.text);
  });

  // AMD red itself is 4.38:1 under white: a button filled with it would ship
  // below the floor. Pinning the gap is what stops a "use the real brand red"
  // edit from going green.
  it('is not the brand red, which cannot carry white text', () => {
    expect(vars.get('--g-accent-fill')).not.toBe(vars.get('--g-brand'));
    expect(contrast([255, 255, 255], paint('--g-brand').rgb)).toBeLessThan(FLOOR.text);
  });
});

// AMD's brand literals. The black ground, white type, AMD red and AMD orange
// are the brand's own values, so a "small tweak" here is a brand change, not a
// design tweak. "The website" in docs/spec/gaia-design-language.mdx.
const PINNED: Array<[string, string]> = [
  ['--g-bg', '#000000'],
  ['--g-text', '#ffffff'],
  ['--g-brand', '#ed1c24'],
  ['--g-rc', '#f26522'],
  ['--g-sky-hot', '#ed1c24'],
];

describe('the palette is AMD brand', () => {
  it.each(PINNED)('%s', (token, expected) => {
    expect(hex(paint(token).rgb)).toBe(expected);
  });

  // Every red role stays red: within a few degrees of the brand hue. A copper,
  // a gold or a pink that slips in reads as a different brand.
  it.each(['--g-accent', '--g-accent2', '--g-accent-text', '--g-accent-fill', '--g-accent-fill2', '--g-focus', '--g-code-accent'])(
    '%s is in the red family',
    (token) => {
      const h = hue(paint(token).rgb)!;
      const off = Math.min(h, 360 - h);
      expect(off, `${token} hue ${h.toFixed(1)}° is ${off.toFixed(1)}° off red`).toBeLessThan(6);
    },
  );

  // The browser-chrome colour is the one canvas value that cannot come from a
  // CSS custom property — a <meta> tag can't read one — so Layout.astro repeats
  // it. Pin it: an edit to --g-bg that misses it leaves the phone status bar on
  // the old canvas, which no page-level assertion would catch.
  it('keeps the theme-color literal in Layout.astro equal to the canvas', () => {
    const src = readFileSync(new URL('../layouts/Layout.astro', import.meta.url), 'utf8');
    const found = new Set([...src.matchAll(/#[0-9a-fA-F]{6}\b/g)].map((m) => m[0].toLowerCase()));
    expect([...found]).toEqual([hex(paint('--g-bg').rgb)]);
  });
});

// The spec calls itself the source of truth and quotes ratios in prose. A
// retuned value leaves the page stating a number that is no longer true, with
// nothing to notice. These recompute every quoted figure.
describe('the design language quotes the ratios its values actually hold', () => {
  const SPEC_PATH = 'docs/spec/gaia-design-language.mdx';
  const spec = readFileSync(
    new URL('../../../docs/spec/gaia-design-language.mdx', import.meta.url),
    'utf8',
  );

  // The shared palette (Agent UI and TUI) is stated in the spec's role table;
  // its quoted ratios must follow from the hexes in that same table.
  const table = new Map<string, [RGB, RGB]>();
  for (const [, role, dark, light] of spec.matchAll(
    /^\|\s*([^|]+?)\s*\|[^|`]*`(#[0-9a-fA-F]{6})`\s*\|[^|`]*`(#[0-9a-fA-F]{6})`\s*\|/gm,
  ))
    table.set(role, [fromHex(dark), fromHex(light)]);

  const SHARED_ROLES = ['Primary text', 'Secondary text', 'Accent / focus'];
  const SHARED_GROUNDS = ['Canvas', 'Raised surface'];

  const headline = /\*\*([\d.]+):1 dark, ([\d.]+):1 light\*\*/.exec(spec);
  const onFill = /carries white at ([\d.]+):1/.exec(spec);
  const band =
    /pale stone measures \*\*([\d.]+):1\*\*[\s\S]{0,60}?warm slate \*\*([\d.]+):1\*\*/.exec(spec);
  const siteRed = /AMD red `#ED1C24` measures \*\*([\d.]+):1\*\*/.exec(spec);
  const siteFill = /deepened to `#D3121A`, which carries white at \*\*([\d.]+):1\*\*/.exec(spec);

  it('still states the sentences this suite checks', () => {
    // Reworded prose must fail loudly rather than skip the cases below.
    expect([...table.keys()]).toEqual(
      expect.arrayContaining([...SHARED_ROLES, ...SHARED_GROUNDS, 'Accent fill']),
    );
    expect(headline, `${SPEC_PATH} no longer says "N:1 dark, N:1 light"`).not.toBeNull();
    expect(onFill, `${SPEC_PATH} no longer says "carries white at N:1"`).not.toBeNull();
    expect(band, `${SPEC_PATH} no longer measures pale stone and warm slate`).not.toBeNull();
    expect(siteRed, `${SPEC_PATH} no longer measures AMD red on the website`).not.toBeNull();
    expect(siteFill, `${SPEC_PATH} no longer states the website's button fill`).not.toBeNull();
  });

  it.each([
    ['dark', 0, 1],
    ['light', 1, 2],
  ] as const)('the shared %s measured minimum', (themeName, column, group) => {
    const ratios = SHARED_ROLES.flatMap((role) =>
      SHARED_GROUNDS.map((ground) => contrast(table.get(role)![column], table.get(ground)![column])),
    );
    const actual = Number(Math.min(...ratios).toFixed(2));
    expect(
      Number(headline![group]),
      `${SPEC_PATH} says ${headline![group]}:1 ${themeName}; its own table measures ${actual}:1`,
    ).toBe(actual);
  });

  it('white on the shared accent fill', () => {
    const actual = Number(contrast([255, 255, 255], table.get('Accent fill')![0]).toFixed(2));
    expect(Number(onFill![1]), `${SPEC_PATH} says ${onFill![1]}:1; the table gives ${actual}:1`).toBe(
      actual,
    );
  });

  it.each([
    ['pale stone on a white terminal', 1, [255, 255, 255] as RGB, 1],
    ['warm slate on a black terminal', 0, [0, 0, 0] as RGB, 2],
  ] as const)('%s', (label, column, terminal, group) => {
    const actual = Number(contrast(table.get('Raised surface')![column], terminal).toFixed(2));
    expect(Number(band![group]), `${SPEC_PATH} says ${band![group]}:1 for ${label}; the table gives ${actual}:1`).toBe(
      actual,
    );
  });

  // The website's own figures are measured off tokens.css.
  it("AMD red's lowest ratio on the website's grounds", () => {
    const actual = Number(
      Math.min(...pageGrounds().map((g) => contrast(paint('--g-brand').rgb, g.rgb))).toFixed(2),
    );
    expect(Number(siteRed![1]), `${SPEC_PATH} says ${siteRed![1]}:1; tokens.css measures ${actual}:1`).toBe(
      actual,
    );
  });

  it("white on the website's button fill", () => {
    const actual = Number(contrast([255, 255, 255], paint('--g-accent-fill').rgb).toFixed(2));
    expect(Number(siteFill![1]), `${SPEC_PATH} says ${siteFill![1]}:1; tokens.css measures ${actual}:1`).toBe(
      actual,
    );
    expect(hex(paint('--g-accent-fill').rgb)).toBe('#d3121a');
  });
});
