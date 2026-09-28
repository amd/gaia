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

/**
 * Electron's own windows. They live outside `/src`, so the two globs above
 * miss them -- which is how the first-launch window kept an indigo canvas and
 * a blue gradient progress bar through a palette migration.
 */
const CJS_SOURCES = {
    ...(import.meta.glob('/*.cjs', {
        query: '?raw',
        import: 'default',
        eager: true,
    }) as Record<string, string>),
    ...(import.meta.glob('/services/*.cjs', {
        query: '?raw',
        import: 'default',
        eager: true,
    }) as Record<string, string>),
};

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

/**
 * Every opaque surface that carries body text. `--bg-active` is deliberately
 * absent: it is the darkest light surface, and `--accent` is pinned to #9A4930
 * across all three surfaces (`website/src/design/cross-surface.test.ts`), which
 * reads 4.15:1 on it. Nothing can fix that pair -- the copper is fixed, and the
 * bg would have to go *lighter* than `--bg-hover` to clear the floor, inverting
 * the depth ramp. Every rule that paints on `--bg-active` today pairs it with
 * `--text-primary` (10.55:1); the guard below catches it if one stops.
 */
const SURFACES = ['--bg-primary', '--bg-card', '--bg-tertiary', '--bg-hover', '--bg-recessed'];

/** A tinted chip paints its own hue as text on a wash of itself. */
const TINTS: Array<[string, string]> = [
    ['--accent', '--accent-dim'],
    ['--accent', '--accent-dim2'],
    ['--accent-green', '--accent-green-dim'],
    ['--accent-yellow', '--accent-yellow-dim'],
    ['--accent-blue', '--accent-blue-dim'],
    ['--danger', '--danger-dim'],
    ['--danger', '--danger-dim2'],
];

const CASES: Case[] = [
    ...ON_SURFACES.flatMap<Case>((t) => SURFACES.map<Case>((s) => [t, s, 'text'])),

    // Deliberately recessive: always duplicates something on the same row.
    ...SURFACES.map<Case>((s) => ['--text-muted', s, 'recessive']),

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

/**
 * The tint chip -- a hue as text on a thin wash of itself -- is the one idiom
 * the pairs guard below cannot see: it filters to opaque backgrounds, so every
 * translucent `-dim` fill is exempt by construction. That is how the whole
 * family shipped at 4.02-4.23:1 in light while the matrix above read green on
 * the same card at 4.70:1.
 */
describe.each(['light', 'dark'] as const)('%s theme tint chips', (theme) => {
    it.each(TINTS)('%s on %s clears the text floor', (fg, dim) => {
        // Judged on the raised surface: it is the darkest one a chip lands on,
        // so clearing it clears the canvas too.
        const back = resolve(theme, dim, resolve(theme, '--bg-card'));
        const ratio = contrast(resolve(theme, fg, back), back);
        expect(
            Number(ratio.toFixed(2)),
            `${theme}: ${fg} on ${dim} over --bg-card is ${ratio.toFixed(2)}:1, floor ${FLOOR.text}:1`,
        ).toBeGreaterThanOrEqual(FLOOR.text);
    });

    it('measures a wash that is actually translucent', () => {
        // An opaque `-dim` would make every case above a plain pair and the
        // blend untested -- the alpha is the whole point of the idiom.
        const opaque = TINTS.filter(([, dim]) => !/^rgba\(/.test({ ...LIGHT, ...DARK }[dim] ?? ''));
        expect(opaque.map(([, d]) => d), 'not a tint -- drop it from TINTS').toEqual([]);
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

    // The mirror of the test above, and the direction that actually bites: an
    // undeclared custom property is not an error anywhere in CSS, it simply
    // paints nothing. A typo'd token, or one dropped from the palette while a
    // rule still asked for it, leaves a borderless panel and a silent suite.
    it('declares every token it consumes', () => {
        const declared = new Set<string>();
        for (const text of Object.values(CSS_SOURCES))
            for (const m of text.matchAll(/^\s*(--[A-Za-z0-9-]+)\s*:/gm)) declared.add(m[1]);
        // PermissionManager hands the cascade a tier's colours as inline style
        // props, so the declaration is a TSX object key rather than a CSS rule.
        for (const text of Object.values(TS_SOURCES))
            for (const m of text.matchAll(/['"](--[A-Za-z0-9-]+)['"]\s*:/g)) declared.add(m[1]);

        // A fallback used to excuse a token from this check, on the reading
        // that it was a deliberate "may not exist". It is not: the fallback
        // silences the typo, so the rule paints the wrong role forever and
        // nothing says so -- `--text-tertiary` is how that was found.
        const undeclared = new Set<string>();
        for (const text of [...Object.values(CSS_SOURCES), ...Object.values(TS_SOURCES)])
            for (const m of text.matchAll(/var\(\s*(--[A-Za-z0-9-]+)\s*[,)]/g))
                if (!declared.has(m[1])) undeclared.add(m[1]);

        expect(
            [...undeclared].sort(),
            'declared nowhere -- a fallback hides this rather than fixing it',
        ).toEqual([]);
    });

    /**
     * The other half: a fallback on a token that *is* declared can never fire,
     * so it is a second value nobody reads and the palette drifts past it. The
     * one real exception is annotated at its site.
     */
    it('carries no fallback that can never fire', () => {
        const CONDITIONAL = new Set(['--filter-color']);
        const declared = new Set<string>();
        for (const text of Object.values(CSS_SOURCES))
            for (const m of text.matchAll(/^\s*(--[A-Za-z0-9-]+)\s*:/gm)) declared.add(m[1]);

        const dead: string[] = [];
        for (const [path, source] of Object.entries(CSS_SOURCES))
            source.split(/\r?\n/).forEach((line: string, i: number) => {
                for (const m of line.matchAll(/var\(\s*(--[A-Za-z0-9-]+)\s*,/g))
                    if (declared.has(m[1]) && !CONDITIONAL.has(m[1]))
                        dead.push(`${path}:${i + 1}  ${m[1]}`);
            });
        expect(dead, 'the token is declared, so this fallback is dead -- drop it').toEqual([]);
    });

    // Shadows carry the theme the way colours do -- the dark palette needs a
    // heavier, blacker drop than the light one -- but their values start with a
    // length, so the colour-role guard above steps straight over them.
    it('declares every shadow in both themes', () => {
        const shadows = Object.keys(LIGHT).filter((t) => t.startsWith('--shadow-'));
        expect(shadows.length, 'no shadow tokens found to check').toBeGreaterThan(0);
        expect(
            shadows.filter((t) => !(t in DARK)),
            'a shadow declared only in :root keeps its light elevation in dark mode',
        ).toEqual([]);
        expect(
            Object.keys(DARK).filter((t) => t.startsWith('--shadow-') && !(t in LIGHT)),
            'a shadow declared only in dark disappears entirely in light mode',
        ).toEqual([]);
    });
});

/**
 * Keyboard focus is the one affordance with no visual fallback: lose the ring
 * and a keyboard user cannot tell where they are, with nothing on screen to
 * hint at it. Both rules are checked because they do different jobs -- the bare
 * one covers links, tabs and anything with `tabindex`, and the element list
 * re-states it for the form controls, which carry a UA outline that otherwise
 * wins.
 */
describe('keyboard focus stays visible', () => {
    const RING = String.raw`outline:\s*2px\s+solid\s+var\(--accent\)`;

    it.each([
        [':focus-visible', String.raw`(?:^|\})\s*:focus-visible\s*\{[^}]*${RING}`],
        [
            'form controls',
            String.raw`button:focus-visible\s*,\s*input:focus-visible\s*,\s*textarea:focus-visible\s*\{[^}]*${RING}`,
        ],
    ])('%s paints an accent ring', (_label, pattern) => {
        expect(
            new RegExp(pattern, 'm').test(INDEX_CSS),
            'the focus ring is gone -- keyboard users lose their place with no visual cue',
        ).toBe(true);
    });

    /**
     * A focus rule may drop the UA outline, but only if it paints something in
     * its place. Most do it with a border or a box-shadow in the same rule; one
     * moves the cue to a wrapper, which is out of reach of a per-rule check and
     * so is named here with where it went.
     */
    const RING_ELSEWHERE: Record<string, { ring: string; why: string }> = {
        '.msg-input': { ring: '.input-box:focus-within', why: 'rings the whole composer' },
        '.msg-input:focus-visible': { ring: '.input-box:focus-within', why: 'rings the whole composer' },
        '.agent-hub-search input': { ring: '.agent-hub-search:focus-within', why: 'rings the search box' },
    };

    const flat = (s: string) => s.replace(/\s+/g, ' ').trim();
    const DROPS_OUTLINE = /outline[^:;]*:\s*(?:none|0)\s*(?:;|$)/;
    const OUTLINE = /outline(?:-color|-width|-style)?/;
    const OTHER_CUE = /border(?:-color|-bottom|-top|-left|-right)?|box-shadow|background(?:-color)?/;

    // Values are pulled out and read, never matched in place: `\s*` before a
    // negative lookahead backtracks to zero width, so `box-shadow: none`
    // satisfies "sets a box-shadow that is not none" and the guard passes
    // on a rule it was written to catch.
    const paints = (body: string, property: RegExp) =>
        [...body.matchAll(new RegExp(String.raw`(?:^|[;{])\s*(${property.source})\s*:([^;}]*)`, 'g'))]
            .some((m) => !/^\s*(?:none|0)\s*$/.test(m[2]));

    const cues = (body: string) => paints(body, OUTLINE) || paints(body, OTHER_CUE);

    it('never removes the outline without painting something in its place', () => {
        const offenders: string[] = [];
        for (const { path, selector, body } of RULES) {
            if (!/focus/.test(selector)) continue;
            if (!DROPS_OUTLINE.test(body)) continue;
            if (cues(body)) continue;
            if (flat(selector) in RING_ELSEWHERE) continue;
            offenders.push(`${path}  ${flat(selector)}`);
        }
        expect(
            offenders,
            'this focus rule leaves no cue at all -- add a border, ring or fill',
        ).toEqual([]);
    });

    /**
     * The quieter half of the same bug: `outline: none` on the *base* selector
     * silently cancels the global `:focus-visible` ring for every state of that
     * element, so the control is unreachable by eye even though no focus rule
     * ever mentions it.
     */
    it('never lets a base rule cancel the global ring with no focus cue of its own', () => {
        const focusRules = RULES.filter((r) => /focus/.test(r.selector) && cues(r.body));
        const covered = (path: string, part: string) =>
            focusRules.some(
                (r) => r.path === path && r.selector.split(',').some((s) => flat(s).startsWith(part)),
            );

        const offenders: string[] = [];
        for (const { path, selector, body } of RULES) {
            if (/focus/.test(selector) || !DROPS_OUTLINE.test(body)) continue;
            if (flat(selector) in RING_ELSEWHERE) continue;
            const bare = selector.split(',').map(flat).filter((p) => !covered(path, p));
            if (bare.length) offenders.push(`${path}  ${bare.join(', ')}`);
        }
        expect(
            offenders,
            'this element kills the global focus ring and never replaces it',
        ).toEqual([]);
    });

    it('keeps the moved-ring list honest -- every entry still drops its outline', () => {
        const stale = Object.keys(RING_ELSEWHERE).filter(
            (s) =>
                !RULES.some(
                    (r) =>
                        r.selector.replace(/\s+/g, ' ').trim() === s &&
                        /outline\s*:\s*(?:none|0)\b/.test(r.body),
                ),
        );
        expect(stale, 'no such rule -- drop the entry').toEqual([]);
    });

    // Excusing a rule is only safe while the ring it points at is still there:
    // delete the wrapper and the exemption keeps the element silently bare.
    it('keeps the moved-ring list honest -- every ring it points at still paints', () => {
        const gone = Object.entries(RING_ELSEWHERE)
            .filter(
                ([, { ring }]) =>
                    !RULES.some(
                        (r) => r.selector.split(',').some((s) => flat(s) === ring) && cues(r.body),
                    ),
            )
            .map(([excused, { ring, why }]) => `${excused} -> ${ring} (${why})`);
        expect(gone, 'the ring this entry points at is gone or paints nothing').toEqual([]);
    });
});

describe('component stylesheets route colour through tokens', () => {
    // Pure-black shadow alphas are not a colour role -- a shadow is the same
    // black everywhere, and the token file owns the per-theme opacities.
    const SHADOW_ONLY =/rgba\(\s*0\s*,\s*0\s*,\s*0\s*,/;
    const LITERAL = /#[0-9a-fA-F]{3,8}\b|rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+/;

    /**
     * `white` paints exactly what `#ffffff` paints, and the hex-only sweep
     * above waved it straight through -- one toggle knob shipped a fixed white
     * while its two siblings rode `--switch-knob`, which is #F0EDE7 in dark.
     *
     * `transparent` and `currentColor` are absent on purpose: neither names a
     * colour, so neither can disagree with a theme.
     */
    const NAMED_COLOUR =
        /(?<![-\w])(?:white|black|red|green|blue|yellow|orange|purple|gray|grey|cyan|magenta|pink|brown|silver|gold|navy|teal|lime|olive|maroon|aqua|fuchsia)(?![-\w])/i;

    /**
     * Only the declaration value, so a property (`white-space`), a class
     * (`.badge.green`) and a token name (`--accent-blue`) cannot be mistaken
     * for a colour being painted.
     */
    const valueOf = (line: string) => (line.includes(':') ? line.slice(line.indexOf(':') + 1) : '');

    // A gradient in `mask-image` is an alpha ramp, not paint: its `black` stop
    // means "fully opaque here". Same exemption the gradient guard carries.
    const MASK_IMAGE = /(?:^|[;{\s])(?:-webkit-)?mask(?:-image)?\s*:/;

    it('has no colour literal outside index.css', () => {
        const offenders: string[] = [];
        for (const [path, source] of Object.entries(CSS_SOURCES)) {
            if (path === INDEX_PATH) continue;
            source
                .replace(/\/\*[\s\S]*?\*\//g, '')
                .split(/\r?\n/)
                .forEach((line: string, i: number) => {
                    const named = !MASK_IMAGE.test(line) && NAMED_COLOUR.test(valueOf(line));
                    if (!named && !LITERAL.test(line)) return;
                    if (!named && SHADOW_ONLY.test(line) && !/(background|^\s*color)\s*:/.test(line))
                        return;
                    offenders.push(`${path}:${i + 1}  ${line.trim()}`);
                });
        }
        expect(offenders, 'route these through a role token in index.css').toEqual([]);
    });

    // A colour in JS is always a quoted string; `#2118` in an issue reference
    // never is, so matching the quote is what keeps comments out of the sweep.
    const QUOTED_COLOUR =
        /['"`]\s*(#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?|rgba?\([^'"`]*\d+\s*,)/;

    // One place legitimately holds a hex: DevTools has no cascade to read a
    // custom property from. Anything rendered into the page does, including a
    // canvas -- `getComputedStyle` resolves custom properties even where
    // `var()` is not accepted, which is how the QR modal dropped its pair.
    const LITERAL_ALLOWED: Record<string, string> = {
        '/src/utils/logger.ts':
            'DevTools `%c` category palette -- never painted in the app',
        '/src/utils/theme.ts':
            '<meta name="theme-color"> has no cascade to read --bg-primary from; both values are pinned to it below',
    };

    // Nothing under `__tests__` is painted, and a test that pins what a token
    // resolves to has to name the value it expects.
    const IS_TEST = /\/__tests__\//;

    it('has no quoted colour literal in components outside the allowlist', () => {
        const offenders: string[] = [];
        for (const [path, source] of Object.entries(TS_SOURCES)) {
            if (path in LITERAL_ALLOWED || IS_TEST.test(path)) continue;
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

describe('the Electron windows mirror the dark theme rather than inventing one', () => {
    // Both windows load from a `data:` URL, so no stylesheet reaches them and
    // `var()` has nothing to resolve against. Each one opens its inline CSS
    // with a `:root` block copying the dark theme by hand, and every rule below
    // it references a role. A copy drifts, so the copy is checked here instead
    // of being left to a comment asking the next editor to keep it in step.
    const DARK_TABLE: Record<string, string> = { ...LIGHT, ...DARK };

    /**
     * Blank comments, keeping the line count so a reported line number is the
     * real one. `//` counts only at line start or after whitespace, so a URL's
     * `https://` survives. Blanking matters more here than in a stylesheet:
     * main.cjs cites issues (`#1388`, `#782`) that read as valid four- and
     * three-digit hex.
     */
    function withoutComments(src: string): string {
        const blank = (m: string) => m.replace(/[^\n]/g, ' ');
        return src
            .replace(/\/\*[\s\S]*?\*\//g, blank)
            .split(/\r?\n/)
            .map((line: string) => line.replace(/(^|\s)\/\/.*$/, '$1'))
            .join('\n');
    }

    /** A `--token: #hex;` line inside one of the mirrored `:root` blocks. */
    const DECLARATION = /^\s*(--[A-Za-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;/;
    const HEX = /#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b/g;
    const NAMED_IN_CJS =
        /(?<![-\w])(?:white|black|red|green|blue|yellow|orange|purple|gray|grey|cyan|magenta|pink|navy|teal|lime|olive|maroon|aqua|fuchsia)(?![-\w])/i;

    /**
     * A literal that cannot be a `var()`. Each entry names the token it copies,
     * so the value is held to index.css like the CSS ones rather than waved
     * through.
     */
    const MIRRORED: Array<{ path: string; literal: string; token: string; why: string }> = [
        {
            path: '/services/backend-installer-progress-dialog.cjs',
            literal: '#17161C',
            token: '--bg-primary',
            why: 'BrowserWindow backgroundColor is a main-process option, not CSS -- there is no cascade to read a custom property from',
        },
    ];

    it('reads the Electron sources, not an empty set', () => {
        // A glob that silently matches nothing makes every check below pass.
        expect(Object.keys(CJS_SOURCES), 'the /*.cjs glob picked up nothing').toContain('/main.cjs');
        expect(Object.keys(CJS_SOURCES).filter((p) => p.startsWith('/services/')).length).toBeGreaterThan(5);
    });

    it('declares every mirrored token at its dark-theme value', () => {
        const wrong: string[] = [];
        for (const [path, source] of Object.entries(CJS_SOURCES)) {
            withoutComments(source)
                .split('\n')
                .forEach((line: string, i: number) => {
                    const m = DECLARATION.exec(line);
                    if (!m) return;
                    const [, token, value] = m;
                    const expected = DARK_TABLE[token];
                    if (expected === undefined)
                        wrong.push(`${path}:${i + 1}  ${token} is not a token index.css declares`);
                    else if (expected.toLowerCase() !== value.toLowerCase())
                        wrong.push(`${path}:${i + 1}  ${token} is ${value} here, ${expected} in index.css`);
                });
        }
        expect(wrong, 'copy the value from the [data-theme="dark"] block of index.css').toEqual([]);
    });

    it('names no other colour', () => {
        const offenders: string[] = [];
        for (const [path, source] of Object.entries(CJS_SOURCES)) {
            const exempt = MIRRORED.filter((e) => e.path === path).map((e) => e.literal);
            withoutComments(source)
                .split('\n')
                .forEach((line: string, i: number) => {
                    if (DECLARATION.test(line)) return;
                    let probe = line;
                    for (const literal of exempt) probe = probe.split(literal).join('');
                    // `white` is as fixed as `#ffffff`, and these windows are
                    // the one place with no stylesheet to correct it.
                    const named = probe.includes(':') && NAMED_IN_CJS.test(probe.slice(probe.indexOf(':') + 1));
                    if (probe.match(HEX) || /\brgba?\(\s*\d/.test(probe) || named)
                        offenders.push(`${path}:${i + 1}  ${line.trim()}`);
                });
        }
        expect(
            offenders,
            "reference a --token from the window's own :root block, or add it to MIRRORED with the token it copies",
        ).toEqual([]);
    });

    it('keeps the exemption list honest -- every entry still holds its literal', () => {
        // Look past the `:root` declarations: the drift check already covers
        // those, so an entry whose only remaining match is one of them is
        // exempting nothing and would sit here forever unnoticed.
        const stale = MIRRORED.filter(({ path, literal }) => {
            const lines = withoutComments(CJS_SOURCES[path] ?? '').split('\n');
            return !lines.some((line: string) => !DECLARATION.test(line) && line.includes(literal));
        }).map(({ path, literal }) => `${path}  ${literal}`);
        expect(stale, 'nothing left to exempt here -- drop the entry').toEqual([]);
    });

    it('holds every exempted literal to the token it mirrors', () => {
        for (const { path, literal, token, why } of MIRRORED) {
            expect(DARK_TABLE[token], `${path}: index.css declares no ${token}`).toBeDefined();
            expect(DARK_TABLE[token].toLowerCase(), `${path}: ${literal} is no longer ${token}`).toBe(
                literal.toLowerCase(),
            );
            expect(why.length, path).toBeGreaterThan(20);
        }
    });
});

describe('the browser frame is painted the same canvas as the app', () => {
    /**
     * `index.html` sits outside `/src`, so the globs above miss it — the same
     * blind spot that let the Electron windows keep an indigo canvas.
     */
    const INDEX_HTML = (
        import.meta.glob('/index.html', {
            query: '?raw',
            import: 'default',
            eager: true,
        }) as Record<string, string>
    )['/index.html'];

    const THEME_TS = TS_SOURCES['/src/utils/theme.ts'];

    it('reads both files, not an empty set', () => {
        expect(INDEX_HTML, 'the /index.html glob picked up nothing').toBeTruthy();
        expect(THEME_TS, 'the /src glob no longer reaches utils/theme.ts').toBeTruthy();
    });

    it('ships the dark canvas as the pre-paint value', () => {
        const meta = /<meta\s+name="theme-color"\s+content="(#[0-9a-fA-F]{3,8})"/.exec(INDEX_HTML);
        expect(meta, 'index.html declares no <meta name="theme-color">').not.toBeNull();
        expect(meta![1].toLowerCase(), 'the frame would open a different colour from the app').toBe(
            DARK['--bg-primary'].toLowerCase(),
        );
    });

    it('rewrites it to the canvas of whichever theme is applied', () => {
        for (const [theme, table] of [
            ['light', LIGHT],
            ['dark', DARK],
        ] as const) {
            const m = new RegExp(`${theme}:\\s*'(#[0-9a-fA-F]{3,8})'`).exec(THEME_TS);
            expect(m, `theme.ts names no ${theme} canvas`).not.toBeNull();
            expect(m![1].toLowerCase(), `${theme}: the frame no longer matches --bg-primary`).toBe(
                table['--bg-primary'].toLowerCase(),
            );
        }
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

    /**
     * Modules that paint into such a pane from JS rather than a stylesheet.
     * The terminal hands each line its colour as an inline style prop, so the
     * rule sweep above never sees it -- which is how `stdout` shipped on
     * `--text-secondary`, a role that flips to near-black on `--bg-code`.
     */
    const CODE_SURFACE_MODULES = ['/src/components/AgentTerminal.tsx'];

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

    it('reads a colour out of every module it claims to police', () => {
        // A renamed or moved module would leave the check below sweeping an
        // empty string and passing on nothing at all.
        for (const path of CODE_SURFACE_MODULES) {
            const source = TS_SOURCES[path];
            expect(source, `${path}: no longer in the /src glob`).toBeTruthy();
            expect(
                [...source.matchAll(/['"]var\(\s*(--[\w-]+)\s*\)['"]/g)].length,
                `${path}: names no token -- it no longer paints from JS`,
            ).toBeGreaterThan(0);
        }
    });

    it('uses no page-theme colour from a module that paints into one', () => {
        const offenders: string[] = [];
        for (const path of CODE_SURFACE_MODULES)
            for (const m of (TS_SOURCES[path] ?? '').matchAll(/['"]var\(\s*(--[\w-]+)\s*\)['"]/g))
                if (FLIPPING.has(m[1])) offenders.push(`${path} -> ${m[1]}`);
        expect(
            [...new Set(offenders)],
            'use the --*-code / --code-* roles: these flip to near-black on --bg-code',
        ).toEqual([]);
    });
});

/**
 * The cases above measure the pairings the palette *intends*. This measures the
 * ones components actually ship, and it is what catches a token that is fine on
 * the canvas and marginal on a chip -- `--accent-green` on `--bg-tertiary` was
 * 4.32:1, below the floor the same token cleared everywhere else.
 *
 * Most rules do not set the surface they land on: a chip modifier picks up the
 * fill from its base class, and a child element sits on its parent's. Matching
 * only rules that set both halves saw 54 pairs and missed ten real ones, all in
 * the second shape.
 */
describe('component rules, on the surface they actually land on', () => {
    /** Recessive by role: always duplicates something on the same row. */
    const RECESSIVE = new Set(['--text-muted', '--text-code-dim']);

    const FG_TOKEN = /(?:^|[;\s])color\s*:\s*var\(\s*(--[A-Za-z0-9-]+)\s*\)/;
    const BG_TOKEN = /background(?:-color)?\s*:\s*var\(\s*(--[A-Za-z0-9-]+)\s*\)/;
    /** Any fill at all, including `transparent`, a literal, or a gradient. */
    const DECLARES_BG = /(?:^|[;\s])background(?:-color)?\s*:/;

    const flat = (s: string) => s.replace(/\s+/g, ' ').trim();

    // A rule whose fill is `transparent` or a gradient is indexed as null on
    // purpose: it has to shadow the var() one further up its selector, or the
    // walk below steps over it and measures a surface nothing paints.
    const OWN_BG = new Map<string, string | null>();
    for (const { path, selector, body } of RULES)
        if (DECLARES_BG.test(body))
            OWN_BG.set(`${path}|${flat(selector)}`, BG_TOKEN.exec(body)?.[1] ?? null);

    /**
     * The surface a rule inherits: the longest selector in the same file that
     * is a prefix of this one -- `.chip` for `.chip.running`, `.card` for
     * `.card .title`. A prefix walk rather than a cascade implementation; it is
     * wrong for a sibling combinator, and right for the modifier and
     * descendant spellings that make up every pair in the tree.
     */
    function inheritedBg(path: string, selector: string): string | null {
        let best: [string, string | null] | null = null;
        for (const [key, token] of OWN_BG) {
            const at = key.indexOf('|');
            if (key.slice(0, at) !== path) continue;
            const base = key.slice(at + 1);
            if (base === selector || !selector.startsWith(base)) continue;
            if (!best || base.length > best[0].length) best = [base, token];
        }
        return best?.[1] ?? null;
    }

    const pairs = RULES.flatMap(({ path, selector, body }) => {
        const fg = FG_TOKEN.exec(body)?.[1];
        if (!fg) return [];
        const own = BG_TOKEN.exec(body)?.[1];
        const bg = own ?? (DECLARES_BG.test(body) ? null : inheritedBg(path, flat(selector)));
        // A translucent or color-mix() fill has no single colour to measure
        // here -- what sits behind it lives in another rule.
        if (!bg || !/^#/.test(LIGHT[bg] ?? '')) return [];
        return [{ path, selector: flat(selector), fg, bg, via: own ? 'sets' : 'inherits' }];
    });

    it('finds pairs to measure', () => {
        expect(pairs.length).toBeGreaterThan(20);
        // Without this the resolver could quietly stop resolving -- the suite
        // would stay green on the half of the pairs it was already seeing.
        expect(
            pairs.filter((p) => p.via === 'inherits').length,
            'the inherited-surface walk resolved nothing',
        ).toBeGreaterThan(20);
    });

    it.each(['light', 'dark'] as const)('%s theme: every pair clears its floor', (theme) => {
        const offenders: string[] = [];
        for (const { path, selector, fg, bg, via } of pairs) {
            const back = resolve(theme, bg);
            const ratio = contrast(resolve(theme, fg, back), back);
            const floor = RECESSIVE.has(fg) ? FLOOR.recessive : FLOOR.text;
            if (ratio < floor)
                offenders.push(
                    `${path}  ${selector} (${via} ${bg}): ${fg} is ${ratio.toFixed(2)}:1, floor ${floor}:1`,
                );
        }
        expect([...new Set(offenders)]).toEqual([]);
    });
});

/**
 * "A hue that carries a status meaning is not available for decoration"
 * (docs/spec/gaia-design-language.mdx). The pairs above ask whether a colour is
 * legible; this asks whether it is *lying*. Four controls shipped in the status
 * red for no reason other than that the retired brand accent happened to be red
 * too -- a tool that was merely running was outlined identically to one that had
 * failed, which is the whole failure mode the status roles exist to prevent.
 */
describe('status hues stay on status', () => {
    /**
     * Built from a list rather than written inline so the names can be checked
     * against the stylesheet. The first version of this guard matched
     * `--warning` -- a token the Agent UI has never declared -- so half of what
     * it claimed to police matched nothing, and nothing said so.
     *
     * Only `--danger` is here today. Widening it to --accent-green/-yellow/
     * -blue/-cyan/-purple surfaces 84 rules that paint a status hue for
     * decoration, which is a repaint across 18 stylesheets rather than a test
     * change; it is listed as a scheduled gap in the design-language spec.
     */
    const STATUS_ROLE_NAMES = ['--danger'];
    const STATUS_ROLES = new RegExp(
        String.raw`var\(\s*(${STATUS_ROLE_NAMES.join('|')})\s*\)`,
        'g',
    );

    it('names only roles the stylesheet actually declares', () => {
        const undeclared = STATUS_ROLE_NAMES.filter(
            (t) => !new RegExp(String.raw`${t}\s*:`).test(CSS_SOURCES[INDEX_PATH]),
        );
        expect(
            undeclared,
            'no such token -- this alternative matches nothing and enforces nothing',
        ).toEqual([]);
    });

    /** A selector naming any of these is claiming a status, so it may paint one. */
    const STATUS_WORDS = [
        'error', 'danger', 'fail', 'warn', 'deny', 'revoke', 'cancel', 'uninstall',
        'delete', 'remove', 'clear', 'confirm', 'stop', 'invalid', 'overdue',
        'urgent', 'critical', 'missing', 'deprecated', 'unsupported', 'bad',
    ];

    /** Status by meaning, not by name. Each entry says which status. */
    const ALSO_STATUS: Record<string, string> = {
        '.btn-retry:hover': 'the retry inside an error banner',
        '.install-confirm-icon': 'the destructive-install confirmation',
        '.mem-cat-badge.reminder': 'a reminder is the attention category',
        '.mem-priority-high': 'highest priority, ranked against the other tints',
        '.mem-success-rate.low': 'a failing success rate',
        '.permission-countdown': 'a grant about to expire',
        '.permission-header': 'the caution surface over a permission prompt',
    };

    // A `[data-theme]` override is the same control in the other theme, so it
    // inherits the entry rather than needing a duplicate one.
    const normalise = (s: string) =>
        s.replace(/\s+/g, ' ').trim().replace(/^\[data-theme="[a-z]+"\]\s+/, '');
    const claimsStatus = (s: string) =>
        STATUS_WORDS.some((w) => s.toLowerCase().includes(w)) || normalise(s) in ALSO_STATUS;

    it('paints no control that is not reporting one', () => {
        const offenders: string[] = [];
        for (const { path, selector, body } of RULES) {
            if (claimsStatus(selector)) continue;
            for (const m of body.matchAll(STATUS_ROLES))
                offenders.push(`${path}  ${normalise(selector)} -> ${m[1]}`);
        }
        expect(
            [...new Set(offenders)],
            'use --accent / --accent-fill: this hue means the thing has failed',
        ).toEqual([]);
    });

    it('keeps the by-meaning list honest -- every entry still matches a rule', () => {
        const stale = Object.keys(ALSO_STATUS).filter(
            (s) => !RULES.some((r) => normalise(r.selector) === s),
        );
        expect(stale, 'no such rule -- drop the entry').toEqual([]);
    });

    it('keeps the word list honest -- every word still excuses a rule', () => {
        // A fresh, non-global copy -- `test()` on the shared one would carry
        // `lastIndex` from the previous rule and skip matches.
        const uses = new RegExp(STATUS_ROLES.source);
        const painted = RULES.filter((r) => uses.test(r.body));
        const stale = STATUS_WORDS.filter(
            (w) => !painted.some((r) => r.selector.toLowerCase().includes(w)),
        );
        expect(stale, 'no rule needs this excuse -- drop the word').toEqual([]);
    });
});

describe('the design language forbids these outright', () => {
    // Everything that paints, including the two Electron windows -- their CSS
    // is a template string rather than a stylesheet, but it reaches a screen
    // the same way, and the install window is where the gradient was found.
    const PAINTED: Record<string, string> = { ...CSS_SOURCES, ...CJS_SOURCES };

    // Hard-coded white survives a theme flip; the fill under it does not.
    // --danger is a deep red on the light canvas but #F2787C on the dark one,
    // so `color: white` on it reads 6.4:1 in one theme and 2.4:1 in the other.
    // --text-inverse flips with the theme, which is why the pairs above test it
    // against every semantic fill.
    it('never paints fill text as a fixed white', () => {
        const offenders: string[] = [];
        for (const [path, source] of Object.entries(PAINTED)) {
            if (path === INDEX_PATH) continue; // owns --accent-fill-text
            source
                .replace(/\/\*[\s\S]*?\*\//g, '')
                .split(/\r?\n/)
                .forEach((line: string, i: number) => {
                    // `color` is not the only property that paints a glyph:
                    // -webkit-text-fill-color wins over it where both are set.
                    if (
                        /(^|[;{])\s*(?:-webkit-text-fill-|text-decoration-|caret-)?color\s*:\s*(white|#fff(?:fff)?)\b/i.test(
                            line,
                        )
                    )
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
    // A zero offset is spellable with a unit, and CSS treats the two as the
    // same length -- matching only the bare `0` let `0px 0px 12px` through.
    const ZERO = String.raw`0(?:\.0+)?(?:px|r?em|%)?`;
    const GLOW_LAYER = new RegExp(String.raw`^\s*(?:inset\s+)?${ZERO}\s+${ZERO}\s+${BLUR}`);
    // drop-shadow() keeps its blur inside the function, where the layer split
    // below cannot see it -- the welcome wordmark's glow was written this way.
    const DROP_SHADOW_GLOW = new RegExp(
        String.raw`drop-shadow\(\s*${ZERO}\s+${ZERO}\s+${BLUR}`,
        'i',
    );

    it('leaves no glow behind', () => {
        const offenders: string[] = [];
        for (const [path, source] of Object.entries(PAINTED)) {
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

    // "No gradient-filled panels" is the line next to "no glow", and until now
    // only the website enforced it -- which is how the install progress bar
    // kept an animated gradient shimmer through a palette migration.
    //
    // A gradient in `mask-image` paints nothing: it is an alpha ramp, used to
    // fade an overflowing label instead of cutting it with an ellipsis. The
    // website guard carries the same exemption for the same reason.
    const MASK = /(?:^|[;{\s])(?:-webkit-)?mask(?:-image)?\s*:/;

    // A gradient is as easy to write in a `style={{…}}` prop as in a rule, and
    // the stylesheet sweep never looks there. Tests are excluded: a guard's own
    // fixture is not something the app paints.
    const PAINTED_AND_INLINE: Record<string, string> = {
        ...PAINTED,
        ...Object.fromEntries(
            Object.entries(TS_SOURCES).filter(([path]) => !/\/__tests__\//.test(path)),
        ),
    };

    it('fills no panel with a gradient', () => {
        const offenders: string[] = [];
        for (const [path, source] of Object.entries(PAINTED_AND_INLINE)) {
            source
                .replace(/\/\*[\s\S]*?\*\//g, '')
                .split(/\r?\n/)
                .forEach((line: string, i: number) => {
                    if (MASK.test(line)) return;
                    if (/(?:linear|radial|conic)-gradient\(/.test(line))
                        offenders.push(`${path}:${i + 1}  ${line.trim()}`);
                });
        }
        expect(offenders, 'use a raised-surface token plus a border').toEqual([]);
    });
});
