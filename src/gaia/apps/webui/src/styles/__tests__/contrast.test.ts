// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Contrast floors for the Agent UI's role tokens.
 *
 * The floors, and the roles they apply to, are the ones published in
 * docs/spec/gaia-design-language.mdx. The website and the TUI enforce the same
 * numbers in their own suites (`tui/internal/ui/theme/contrast_test.go` carries
 * the Go original of the WCAG maths below), so a role cannot be legible in one
 * surface and marginal in another.
 *
 * The tokens are read straight out of `src/styles/index.css` rather than
 * through `getComputedStyle`: jsdom does not run the cascade for custom
 * properties, so resolving them in a fake DOM would test the fake, not the
 * stylesheet that ships.
 */

import { describe, expect, it } from 'vitest';

/**
 * Sources are pulled in through Vite's glob rather than `node:fs`, so the
 * suite type-checks under the app's own browser tsconfig — the same `tsc` run
 * that gates the build.
 */
const CSS_SOURCES = import.meta.glob('/src/**/*.css', {
    query: '?raw',
    import: 'default',
    eager: true,
}) as Record<string, string>;

const TS_SOURCES = import.meta.glob('/src/**/*.{ts,tsx}', {
    query: '?raw',
    import: 'default',
    eager: true,
}) as Record<string, string>;

const INDEX_PATH = '/src/styles/index.css';
const INDEX_CSS = CSS_SOURCES[INDEX_PATH];
if (!INDEX_CSS) throw new Error(`the glob did not pick up ${INDEX_PATH}`);

// ── WCAG 2.1 relative luminance and contrast ────────────────────────────────

function channel(v: number): number {
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
}

function luminance([r, g, b]: RGB): number {
    return 0.2126 * channel(r / 255) + 0.7152 * channel(g / 255) + 0.0722 * channel(b / 255);
}

function contrast(a: RGB, b: RGB): number {
    const [hi, lo] = luminance(a) >= luminance(b) ? [a, b] : [b, a];
    return (luminance(hi) + 0.05) / (luminance(lo) + 0.05);
}

// ── Token parsing ───────────────────────────────────────────────────────────

type RGB = [number, number, number];

/** Declarations inside one top-level rule of index.css, comments stripped. */
function parseBlock(selector: string): Record<string, string> {
    const at = INDEX_CSS.indexOf(selector + ' {');
    if (at < 0) throw new Error(`index.css has no "${selector}" block`);
    const open = INDEX_CSS.indexOf('{', at);
    const close = INDEX_CSS.indexOf('\n}', open);
    const body = INDEX_CSS.slice(open + 1, close).replace(/\/\*[\s\S]*?\*\//g, '');
    const out: Record<string, string> = {};
    for (const line of body.split('\n')) {
        const m = line.match(/^\s*(--[A-Za-z0-9-]+)\s*:\s*(.+?);\s*$/);
        if (m) out[m[1]] = m[2].trim();
    }
    return out;
}

const LIGHT = parseBlock(':root');
const DARK = parseBlock('[data-theme="dark"]');

function hexToRgb(hex: string): RGB {
    const h = hex.length === 4 ? hex.replace(/[0-9a-f]/gi, (c) => c + c) : hex;
    return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
}

function blend(fg: RGB, alpha: number, bg: RGB): RGB {
    return [0, 1, 2].map((i) => Math.round(fg[i] * alpha + bg[i] * (1 - alpha))) as RGB;
}

/**
 * Resolve a token to the colour it actually paints. `over` is what sits behind
 * it, which matters for the alpha tints — a token nobody can see through is
 * unaffected by it.
 */
function resolve(theme: 'light' | 'dark', token: string, over?: RGB): RGB {
    const table = theme === 'dark' ? { ...LIGHT, ...DARK } : LIGHT;
    const raw = table[token];
    if (raw === undefined) throw new Error(`${theme}: token ${token} is not declared`);
    const rgba = raw.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/);
    if (rgba) {
        const base: RGB = [Number(rgba[1]), Number(rgba[2]), Number(rgba[3])];
        const alpha = rgba[4] === undefined ? 1 : Number(rgba[4]);
        if (alpha === 1) return base;
        if (!over) throw new Error(`${token} is translucent; the caller must say what is behind it`);
        return blend(base, alpha, over);
    }
    if (/^#[0-9a-f]{3}$|^#[0-9a-f]{6}$/i.test(raw)) return hexToRgb(raw);
    throw new Error(`${theme}: token ${token} is not a plain colour (${raw})`);
}

// ── The floors ──────────────────────────────────────────────────────────────

const FLOOR = { text: 4.5, recessive: 3.0, chrome: 1.5, onFill: 4.5 } as const;

/** [foreground, background, floor] — the background is the surface it sits on. */
type Case = [string, string, keyof typeof FLOOR];

/** Text and accents, judged against the canvas and against a raised surface. */
const ON_SURFACES = [
    '--text-primary',
    '--text-secondary',
    '--accent',
    '--accent-green',
    '--accent-yellow',
    '--accent-blue',
    '--accent-cyan',
    '--accent-purple',
    '--danger',
];

const CASES: Case[] = [
    ...ON_SURFACES.flatMap<Case>((t) => [
        [t, '--bg-primary', 'text'],
        [t, '--bg-card', 'text'],
    ]),

    // Deliberately recessive: always duplicates something on the same row.
    ['--text-muted', '--bg-primary', 'recessive'],
    ['--text-muted', '--bg-card', 'recessive'],

    // A code pane is its own canvas in both themes, so it is judged against
    // itself rather than against the page behind it.
    ['--text-code', '--bg-code', 'text'],
    ['--text-code', '--bg-code-raised', 'text'],
    ['--code-accent', '--bg-code', 'text'],
    ['--code-success', '--bg-code', 'text'],
    ['--code-warning', '--bg-code', 'text'],
    ['--code-danger', '--bg-code', 'text'],
    ['--code-info', '--bg-code', 'text'],
    ['--text-code-dim', '--bg-code', 'recessive'],
    ['--text-code-dim', '--bg-code-raised', 'recessive'],
    ['--border-code', '--bg-code', 'chrome'],
    ['--border-code', '--bg-code-raised', 'chrome'],

    // Chrome: carries no information, only has to stay visible. Each rule is
    // judged against the surface it is drawn on, which is why there are three
    // of them rather than one.
    ['--border', '--bg-primary', 'chrome'],
    ['--border-light', '--bg-card', 'chrome'],
    ['--border-recessed', '--bg-recessed', 'chrome'],
    ['--switch-knob', '--border', 'chrome'],

    // The sunk pane is not held to a floor against its parent: no fill can
    // clear 1.5:1 below the dark canvas, since even pure black is 1.17:1
    // against it. Its rule carries the depth cue; the fill only has to keep
    // the text on it legible.
    ['--text-primary', '--bg-recessed', 'text'],
    ['--text-secondary', '--bg-recessed', 'text'],

    // Filled pairs. Each covers its own background, so it is judged against
    // itself. --text-inverse flips with the theme so it can ride a semantic
    // token, which flips too.
    ['--accent-fill-text', '--accent-fill', 'onFill'],
    ['--accent-fill-text', '--accent-fill-hover', 'onFill'],
    ['--text-inverse', '--danger', 'onFill'],
    ['--text-inverse', '--accent', 'onFill'],
    ['--text-inverse', '--accent-yellow', 'onFill'],
    ['--text-inverse', '--accent-green', 'onFill'],
];

describe.each(['light', 'dark'] as const)('%s theme contrast floors', (theme) => {
    it.each(CASES)('%s on %s clears the %s floor', (fg, bg, kind) => {
        const back = resolve(theme, bg);
        const ratio = contrast(resolve(theme, fg, back), back);
        expect(
            Number(ratio.toFixed(2)),
            `${theme}: ${fg} on ${bg} is ${ratio.toFixed(2)}:1, floor ${FLOOR[kind]}:1`,
        ).toBeGreaterThanOrEqual(FLOOR[kind]);
    });
});

// ── Structural guards ───────────────────────────────────────────────────────

/** Shared on purpose: the surface they paint does not change with the theme. */
const SHARED_WITH_LIGHT = new Set([
    '--bg-code',
    '--bg-code-raised',
    '--border-code',
    '--text-code',
    '--text-code-dim',
    '--code-accent',
    '--code-success',
    '--code-warning',
    '--code-danger',
    '--code-info',
]);

describe('token declarations', () => {
    it('declares every colour role in both themes', () => {
        const colourish = (v: string) => /^#|^rgba?\(/.test(v);
        const missing = Object.keys(LIGHT)
            .filter((t) => colourish(LIGHT[t]))
            .filter((t) => !(t in DARK) && !SHARED_WITH_LIGHT.has(t));
        expect(
            missing,
            'a colour declared only in :root silently serves dark mode too',
        ).toEqual([]);
    });

    it('leaves no token declared but unused', () => {
        const used = new Set<string>();
        for (const text of [...Object.values(CSS_SOURCES), ...Object.values(TS_SOURCES)])
            for (const m of text.matchAll(/var\(\s*(--[A-Za-z0-9-]+)/g)) used.add(m[1]);

        const dead = [...new Set([...Object.keys(LIGHT), ...Object.keys(DARK)])].filter(
            (t) => !used.has(t),
        );
        expect(dead, 'declared but never consumed').toEqual([]);
    });
});

describe('component stylesheets route colour through tokens', () => {
    // Pure-black shadow alphas are not a colour role -- a shadow is the same
    // black everywhere, and the token file owns the per-theme opacities.
    const SHADOW_ONLY =/rgba\(\s*0\s*,\s*0\s*,\s*0\s*,/;
    const LITERAL = /#[0-9a-fA-F]{3,8}\b|rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+/;

    it('has no colour literal outside index.css', () => {
        const offenders: string[] = [];
        for (const [path, source] of Object.entries(CSS_SOURCES)) {
            if (path === INDEX_PATH) continue;
            source
                .replace(/\/\*[\s\S]*?\*\//g, '')
                .split(/\r?\n/)
                .forEach((line: string, i: number) => {
                    if (!LITERAL.test(line)) return;
                    if (SHADOW_ONLY.test(line) && !/(background|^\s*color)\s*:/.test(line)) return;
                    offenders.push(`${path}:${i + 1}  ${line.trim()}`);
                });
        }
        expect(offenders, 'route these through a role token in index.css').toEqual([]);
    });

    // A colour in JS is always a quoted string; `#2118` in an issue reference
    // never is, so matching the quote is what keeps comments out of the sweep.
    const QUOTED_COLOUR =
        /['"`]\s*(#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?|rgba?\([^'"`]*\d+\s*,)/;

    // Two places legitimately hold a hex: neither can read a CSS custom
    // property at the point it needs the value.
    const LITERAL_ALLOWED: Record<string, string> = {
        '/src/utils/logger.ts':
            'DevTools `%c` category palette -- never painted in the app',
        '/src/components/MobileAccessModal.tsx':
            'QR raster needs a resolved two-tone pair; toCanvas cannot take var()',
    };

    it('has no quoted colour literal in components outside the allowlist', () => {
        const offenders: string[] = [];
        for (const [path, source] of Object.entries(TS_SOURCES)) {
            if (path in LITERAL_ALLOWED) continue;
            source.split(/\r?\n/).forEach((line: string, i: number) => {
                if (QUOTED_COLOUR.test(line)) offenders.push(`${path}:${i + 1}  ${line.trim()}`);
            });
        }
        expect(offenders, 'use a role token, or add the file to LITERAL_ALLOWED with a reason').toEqual(
            [],
        );
    });

    it('keeps the allowlist honest -- every entry still holds a literal', () => {
        const stale = Object.keys(LITERAL_ALLOWED).filter((path) => {
            const source = TS_SOURCES[path];
            return !source || !source.split(/\r?\n/).some((line: string) => QUOTED_COLOUR.test(line));
        });
        expect(stale, 'no literal left here -- drop the allowlist entry').toEqual([]);
    });
});

/** Every rule in every component stylesheet, comments stripped. */
const RULES = Object.entries(CSS_SOURCES).flatMap(([path, source]) =>
    [...source.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({
        path,
        selector: m[1].trim(),
        body: m[2],
    })),
);

describe('code surfaces stay on the code roles', () => {
    /**
     * Selector prefixes that only ever paint inside a pane whose canvas is
     * `--bg-code` — dark in both themes. Chrome *around* such a pane (the
     * terminal's header, toolbar and tabs; the file browser's preview header)
     * sits on the page and is deliberately absent.
     */
    const CODE_SURFACES = [
        '.cmd-',
        '.terminal-content',
        '.terminal-line',
        '.terminal-empty',
        '.code-block',
        '.code-header',
        '.code-lang',
        '.code-copy',
        '.fb-preview-content',
        '.mem-log',
    ];

    /**
     * A colour that changes with the theme. On a pane that does not, it is
     * the defect: `--text-primary` flips to near-black and lands at 1.17:1 on
     * the code canvas. The mode-invariant roles are the ones declared once.
     */
    const FLIPPING = new Set(
        Object.keys(LIGHT).filter((t) => /^#|^rgba?\(/.test(LIGHT[t]) && t in DARK),
    );

    /** Caught by a prefix but painted on the page -- the name lies. */
    const EXEMPT: Record<string, string> = {
        '.terminal-line-count': 'the header\'s "N lines" tally, not a line in the pane',
    };

    const onCodeSurface = RULES.filter(
        (r) => CODE_SURFACES.some((p) => r.selector.includes(p)) && !(r.selector in EXEMPT),
    );

    it('keeps the exemptions honest -- every one still names a rule', () => {
        const stale = Object.keys(EXEMPT).filter(
            (s) => !RULES.some((r) => r.selector === s),
        );
        expect(stale, 'no such rule -- drop the exemption').toEqual([]);
    });

    it('uses no page-theme colour on a pane that stays dark', () => {
        const offenders: string[] = [];
        for (const rule of onCodeSurface)
            for (const m of rule.body.matchAll(/var\(\s*(--[A-Za-z0-9-]+)/g))
                if (FLIPPING.has(m[1]))
                    offenders.push(`${rule.path}  ${rule.selector} -> ${m[1]}`);
        expect(
            [...new Set(offenders)],
            'use the --*-code / --code-* roles: these flip to near-black on --bg-code',
        ).toEqual([]);
    });

    it('keeps the surface list honest -- every prefix still matches a rule', () => {
        const stale = CODE_SURFACES.filter(
            (p) => !RULES.some((r) => r.selector.includes(p)),
        );
        expect(stale, 'renamed or deleted -- drop the prefix').toEqual([]);
    });
});

/**
 * The cases above measure the pairings the palette *intends*. This measures the
 * ones components actually ship: wherever one rule sets both a colour and the
 * surface under it, that exact pair has to clear its floor. It is what catches a
 * token that is fine on the canvas and marginal on a chip -- `--accent-green` on
 * `--bg-tertiary` is 4.32:1, below the floor the same token clears everywhere else.
 */
describe('component rules that set their own background', () => {
    /** Recessive by role: always duplicates something on the same row. */
    const RECESSIVE = new Set(['--text-muted', '--text-code-dim']);

    const pairs = RULES.flatMap(({ path, selector, body }) => {
        const fg = /(?:^|[;\s])color\s*:\s*var\(\s*(--[A-Za-z0-9-]+)\s*\)/.exec(body);
        const bg = /background(?:-color)?\s*:\s*var\(\s*(--[A-Za-z0-9-]+)\s*\)/.exec(body);
        // A translucent or color-mix() fill has no single colour to measure
        // here -- what sits behind it lives in another rule.
        return fg && bg && /^#/.test(LIGHT[bg[1]] ?? '') ? [{ path, selector, fg: fg[1], bg: bg[1] }] : [];
    });

    it('finds pairs to measure', () => expect(pairs.length).toBeGreaterThan(20));

    it.each(['light', 'dark'] as const)('%s theme: every pair clears its floor', (theme) => {
        const offenders: string[] = [];
        for (const { path, selector, fg, bg } of pairs) {
            const back = resolve(theme, bg);
            const ratio = contrast(resolve(theme, fg, back), back);
            const floor = RECESSIVE.has(fg) ? FLOOR.recessive : FLOOR.text;
            if (ratio < floor)
                offenders.push(
                    `${path}  ${selector}: ${fg} on ${bg} is ${ratio.toFixed(2)}:1, floor ${floor}:1`,
                );
        }
        expect([...new Set(offenders)]).toEqual([]);
    });
});

describe('the design language forbids these outright', () => {
    // Hard-coded white survives a theme flip; the fill under it does not.
    // --danger is a deep red on the light canvas but #F2787C on the dark one,
    // so `color: white` on it reads 6.4:1 in one theme and 2.4:1 in the other.
    // --text-inverse flips with the theme, which is why the pairs above test it
    // against every semantic fill.
    it('never paints fill text as a fixed white', () => {
        const offenders: string[] = [];
        for (const [path, source] of Object.entries(CSS_SOURCES)) {
            if (path === INDEX_PATH) continue; // owns --accent-fill-text
            source
                .replace(/\/\*[\s\S]*?\*\//g, '')
                .split(/\r?\n/)
                .forEach((line: string, i: number) => {
                    if (/(^|[;{])\s*color\s*:\s*(white|#fff(?:fff)?)\b/i.test(line))
                        offenders.push(`${path}:${i + 1}  ${line.trim()}`);
                });
        }
        expect(offenders, 'use --text-inverse, or --accent-fill-text on --accent-fill').toEqual([]);
    });

    // "No glow" is a line in the spec, and a shadow with no offset but a real
    // blur is the only way to write one. `0 0 0 3px` is a ring, not a glow --
    // its third value is the blur, and a ring's blur is zero.
    // A sub-pixel blur (`0 0 0.4px`) cannot halo -- that spelling is the
    // faux-bold trick, thickening a label without reflowing the row.
    const BLUR = String.raw`[1-9]`;
    const GLOW_LAYER = new RegExp(String.raw`^\s*(?:inset\s+)?0\s+0\s+${BLUR}`);
    // drop-shadow() keeps its blur inside the function, where the layer split
    // below cannot see it -- the welcome wordmark's glow was written this way.
    const DROP_SHADOW_GLOW = new RegExp(String.raw`drop-shadow\(\s*0\s+0\s+${BLUR}`, 'i');

    it('leaves no glow behind', () => {
        const offenders: string[] = [];
        for (const [path, source] of Object.entries(CSS_SOURCES)) {
            source
                .replace(/\/\*[\s\S]*?\*\//g, '')
                .split(/\r?\n/)
                .forEach((line: string, i: number) => {
                    const flag = () => offenders.push(`${path}:${i + 1}  ${line.trim()}`);
                    if (DROP_SHADOW_GLOW.test(line)) return flag();
                    let value = /(?:box|text)-shadow\s*:([^;]*)/.exec(line)?.[1];
                    if (!value) return;
                    // Split on the commas between layers, not the ones inside a
                    // colour function -- so strip the functions first, innermost
                    // out, because color-mix() nests var().
                    for (let prev = ''; prev !== value; ) {
                        prev = value;
                        value = value.replace(/\([^()]*\)/g, '');
                    }
                    if (value.split(',').some((l) => GLOW_LAYER.test(l))) flag();
                });
        }
        expect(offenders, 'docs/spec/gaia-design-language.mdx: no glow').toEqual([]);
    });
});
