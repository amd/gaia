// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// "A surface asks for a role and never for a hex literal" — the first rule in
// docs/spec/gaia-design-language.mdx, made enforceable. tokens.contrast.test.ts
// measures what the tokens owe the reader; this file makes sure a component
// cannot opt out of them by writing the colour inline, which is the only way a
// value can reach the screen unmeasured.
//
// The Agent UI carries the same guard for its own tree in
// src/gaia/apps/webui/src/styles/__tests__/contrast.test.ts — same allowlist
// shape, same reason-per-entry discipline.

import { describe, expect, it } from 'vitest';

/**
 * Stylesheets are read as text through Vite's glob rather than `node:fs`, so
 * the suite type-checks under the site's own tsconfig — the same `astro check`
 * run that gates the build.
 */
const SOURCES = import.meta.glob('/src/**/*.{css,astro}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

/** The one file allowed to hold literals: it is where the roles are defined. */
const TOKENS_PATH = '/src/design/tokens.css';

// ------------------------------------------------------------------ stripping

/**
 * Blank out comments, preserving line count so an offender's reported line
 * number is the real one. `//` is only treated as a comment at the start of a
 * line or after whitespace, so `https://…` survives intact.
 *
 * The split has to eat the `\r` as well as the `\n`: JS treats a bare `\r` as
 * a line terminator, so `.*$` cannot reach past one and every `//` comment in
 * a CRLF file would survive the strip.
 */
function withoutComments(src: string): string {
  const blank = (m: string) => m.replace(/[^\n]/g, ' ');
  return src
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(/<!--[\s\S]*?-->/g, blank)
    .split(/\r?\n/)
    .map((line) => line.replace(/(^|\s)\/\/.*$/, '$1'))
    .join('\n');
}

// ------------------------------------------------------------------- literals

/** #rgb, #rgba, #rrggbb, #rrggbbaa. */
const HEX = /#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b/;

/**
 * `rgb(` / `rgba(` with literal channels. `rgb(var(--g-accent) / 0.4)` is the
 * sanctioned spelling and is not a literal — the digit after the paren is what
 * separates the two.
 */
const RGB_FN = /\brgba?\(\s*\d/;

/** Named CSS colours, as either a declaration value or a Tailwind utility. */
const NAMED = [
  'white', 'black', 'red', 'green', 'blue', 'yellow', 'orange', 'purple',
  'gray', 'grey', 'silver', 'gold', 'cyan', 'magenta', 'pink', 'brown',
  'navy', 'teal', 'lime', 'maroon', 'olive', 'aqua', 'fuchsia', 'beige',
  'ivory', 'tan', 'coral', 'salmon', 'khaki', 'violet', 'indigo', 'crimson',
  // Tailwind's own palette names, which reach the screen the same way.
  'slate', 'zinc', 'neutral', 'stone', 'amber', 'emerald', 'sky', 'rose',
].join('|');

/** `color: white`, `border-bottom-color: black`, … */
const NAMED_DECL = new RegExp(
  String.raw`[\w-]*(?:color|background|fill|stroke|outline|shadow)[\w-]*\s*:[^;{]*\b(?:${NAMED})\b`,
  'i',
);

/** `text-white`, `bg-black/90`, `border-slate-700`, … */
const NAMED_UTILITY = new RegExp(
  String.raw`\b(?:bg|text|border|fill|stroke|ring|outline|shadow|from|via|to|decoration|placeholder|divide|caret|accent)-(?:${NAMED})\b`,
);

/**
 * Genuinely unavoidable literals. Each entry exempts one exact string in one
 * file and says why a role token cannot carry it; anything else on the same
 * line is still checked.
 */
const ALLOWED: Array<{ path: string; literal: string; why: string }> = [
  {
    path: '/src/layouts/Layout.astro',
    literal: '#17161C',
    why: '<meta name="theme-color"> cannot read a custom property; pinned to --g-bg by tokens.contrast.test.ts',
  },
  {
    path: '/src/layouts/Layout.astro',
    literal: '#F5F2EC',
    why: 'same <meta>, light canvas',
  },
  {
    path: '/src/components/ThemeToggle.astro',
    literal: '#17161C',
    why: 'the toggle rewrites that same <meta> from JS, where var() does not resolve',
  },
  {
    path: '/src/components/ThemeToggle.astro',
    literal: '#F5F2EC',
    why: 'same <meta>, light canvas',
  },
  {
    path: '/src/pages/hub/[id].astro',
    literal: 'bg-black/',
    why: 'fullscreen lightbox scrim — pure black in both themes, not a theme colour',
  },
];

/** Drop this file's allowlisted strings from a line before checking it. */
function stripAllowed(path: string, line: string): string {
  let out = line;
  for (const entry of ALLOWED) {
    if (entry.path === path) out = out.split(entry.literal).join('');
  }
  return out;
}

/**
 * Custom-property *names* are not values: `var(--g-code-green)` contains the
 * word "green" but paints whatever the token says. Blank the identifiers so
 * the named-colour sweep reads declarations, not token names.
 */
const withoutVarNames = (line: string): string => line.replace(/--[A-Za-z0-9-]+/g, '');

function offendersIn(path: string, source: string): string[] {
  const found: string[] = [];
  withoutComments(source)
    .split(/\r?\n/)
    .forEach((line, i) => {
      const probe = withoutVarNames(stripAllowed(path, line));
      if (HEX.test(probe) || RGB_FN.test(probe) || NAMED_DECL.test(probe) || NAMED_UTILITY.test(probe))
        found.push(`${path}:${i + 1}  ${line.trim()}`);
    });
  return found;
}

// --------------------------------------------------------------------- checks

describe('the glob that everything below depends on', () => {
  it('picks up the stylesheets, not an empty set', () => {
    expect(SOURCES[TOKENS_PATH], `${TOKENS_PATH} was not read`).toBeTruthy();
    // A silent glob failure would make every assertion below vacuously pass.
    expect(Object.keys(SOURCES).filter((p) => p.endsWith('.astro')).length).toBeGreaterThan(5);
    // Probe global.css for structure, not for any one rule: a rule can be
    // deleted legitimately, and this failing for that reason would send the
    // reader hunting a glob bug that isn't there.
    expect(SOURCES['/src/design/global.css']).toContain('@tailwind');
  });
});

describe('colour reaches the screen through a role, never a literal', () => {
  it('has no colour literal outside tokens.css', () => {
    const offenders = Object.entries(SOURCES)
      .filter(([path]) => path !== TOKENS_PATH)
      .flatMap(([path, source]) => offendersIn(path, source));
    expect(
      offenders,
      'add a role to tokens.css and reference it, or add the literal to ALLOWED with a reason',
    ).toEqual([]);
  });

  it('keeps the allowlist honest — every entry still holds its literal', () => {
    const stale = ALLOWED.filter(({ path, literal }) => !SOURCES[path]?.includes(literal)).map(
      ({ path, literal }) => `${path}  ${literal}`,
    );
    expect(stale, 'nothing left to exempt here — drop the entry').toEqual([]);
  });

  it('every allowlist entry says why', () => {
    for (const entry of ALLOWED) expect(entry.why.length, entry.path).toBeGreaterThan(20);
  });
});

describe('the design language forbids these outright', () => {
  // "No glow, no simulated typing, no decorative blinking, no gradient-filled
  // panels" — docs/spec/gaia-design-language.mdx, Typography and spacing.
  it('fills no panel with a gradient', () => {
    const offenders: string[] = [];
    for (const [path, source] of Object.entries(SOURCES)) {
      withoutComments(source)
        .split(/\r?\n/)
        .forEach((line, i) => {
          if (/(?:linear|radial|conic)-gradient\(/.test(line) || /\bbg-gradient-to-/.test(line))
            offenders.push(`${path}:${i + 1}  ${line.trim()}`);
        });
    }
    expect(offenders, 'use a raised-surface token plus a border').toEqual([]);
  });
});
