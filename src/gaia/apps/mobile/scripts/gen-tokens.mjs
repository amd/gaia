// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Generates src/ui/tokens/generated.ts from the web UI's CSS, so the app's
// colors, type and motion are never hand-copied.
//   node scripts/gen-tokens.mjs           write the file
//   node scripts/gen-tokens.mjs --check   exit 1 if the committed file is stale
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const stylesDir = resolve(here, '../../webui/src/styles');
const outFile = resolve(here, '../src/ui/tokens/generated.ts');
const SOURCES = ['src/gaia/apps/webui/src/styles/index.css', 'src/gaia/apps/webui/src/styles/palettes.css'];

const fail = (msg) => {
    console.error(`gen-tokens: ${msg}`);
    process.exit(1);
};

const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');

/** Every `selector { declarations }` block without nested braces, in source order. */
function blocks(css) {
    const out = [];
    const re = /([^{}]+)\{([^{}]*)\}/g;
    let m;
    while ((m = re.exec(css))) out.push({ selector: m[1].trim().replace(/\s+/g, ' '), body: m[2] });
    return out;
}

function declarations(body) {
    const out = new Map();
    for (const part of body.split(';')) {
        const i = part.indexOf(':');
        if (i < 0) continue;
        const name = part.slice(0, i).trim();
        if (!name.startsWith('--')) continue;
        out.set(
            name,
            part
                .slice(i + 1)
                .trim()
                .replace(/\s+/g, ' '),
        );
    }
    return out;
}

const camel = (name) => name.replace(/^--/, '').replace(/-([a-z0-9])/g, (_, c) => c.toUpperCase());

// Colors that exist once, for both themes (a code block is its own dark canvas).
const SHARED_COLOR =
    /^--(bg-code|bg-code-raised|border-code|text-code|text-code-dim|code-(accent|success|warning|danger))$/;
const COLOR_ROLE = /^--(bg|text|border|accent|danger|tint|code|switch)-?[a-z0-9-]*$/;
const COLOR_VALUE = /^(#[0-9a-fA-F]{3,8}|rgba?\([0-9., %]+\))$/;
const SHADOW = /^--shadow-(sm|md|lg|press)$/;

// Desktop window chrome, the web's hamburger offset, and the cursor-blink easing.
const IGNORED = new Map([
    ['--titlebar-h', 'desktop window chrome'],
    ['--titlebar-controls-w', 'desktop window chrome'],
    ['--sidebar-toggle-clearance', "the web's hamburger offset"],
    ['--ease-step', 'cursor blink, not used on phones'],
]);

const index = stripComments(readFileSync(resolve(stylesDir, 'index.css'), 'utf8'));
const palettesCss = stripComments(readFileSync(resolve(stylesDir, 'palettes.css'), 'utf8'));
const indexBlocks = blocks(index);

const find = (list, selector) => {
    const hit = list.find((b) => b.selector === selector);
    if (!hit) fail(`index.css has no \`${selector}\` block; upstream moved it, update scripts/gen-tokens.mjs.`);
    return hit;
};

const lightDecl = declarations(find(indexBlocks, ':root').body);
const darkDecl = declarations(find(indexBlocks, '[data-theme="dark"]').body);

const colors = { light: {}, dark: {} };
const shadows = { light: {}, dark: {} };
const radius = {};
const type = {};
const faces = {};
const layout = {};
const motion = { easing: {}, duration: {} };

function parseColor(name, value) {
    if (!COLOR_VALUE.test(value)) fail(`cannot parse ${name}: ${value}`);
    return value;
}

const firstFamily = (stack, decls) => {
    let s = stack;
    const v = s.match(/^var\((--[a-z-]+)\)$/);
    if (v) {
        if (!decls.has(v[1])) fail(`${stack} refers to an unknown property`);
        s = decls.get(v[1]);
    }
    return s
        .split(',')[0]
        .trim()
        .replace(/^['"]|['"]$/g, '');
};

const toMs = (name, v) => {
    const m = v.match(/^([0-9.]+)(ms|s)$/);
    if (!m) fail(`cannot parse ${name}: ${v}`);
    return Math.round(Number(m[1]) * (m[2] === 's' ? 1000 : 1));
};

function classify(name, value, theme) {
    if (IGNORED.has(name)) return;
    if (SHADOW.test(name)) {
        shadows[theme][camel(name.replace('--shadow-', ''))] = value;
        return;
    }
    if (COLOR_ROLE.test(name) && !/^--(radius|font|fs|duration|ease|sidebar|content|stagger|toast)/.test(name)) {
        colors[theme][camel(name)] = parseColor(name, value);
        return;
    }
    if (theme === 'dark')
        fail(`${name} in the dark block is not a color role or shadow; map it in scripts/gen-tokens.mjs.`);
    let m;
    if ((m = name.match(/^--radius-(xs|sm|md|lg|xl|full)$/))) {
        const px = value.match(/^([0-9]+)px$/);
        if (!px) fail(`cannot parse ${name}: ${value}`);
        radius[m[1]] = Number(px[1]);
    } else if ((m = name.match(/^--fs-([a-z]+)$/))) {
        const px = value.match(/^([0-9.]+)px$/);
        if (!px) fail(`cannot parse ${name}: ${value}`);
        type[m[1]] = Number(px[1]);
    } else if ((m = name.match(/^--font-(sans|mono|display|greeting)$/))) {
        faces[m[1]] = firstFamily(value, lightDecl);
    } else if (name === '--sidebar-w' || name === '--content-w') {
        const px = value.match(/^([0-9]+)px$/);
        if (!px) fail(`cannot parse ${name}: ${value}`);
        layout[name === '--sidebar-w' ? 'sidebarWidth' : 'contentWidth'] = Number(px[1]);
    } else if ((m = name.match(/^--ease(-[a-z-]+)?$/))) {
        const key = m[1] ? camel(m[1]) : 'standard';
        if (value === 'linear') motion.easing[key] = 'linear';
        else {
            const b = value.match(/^cubic-bezier\(\s*([-0-9.]+),\s*([-0-9.]+),\s*([-0-9.]+),\s*([-0-9.]+)\s*\)$/);
            if (!b) fail(`cannot parse ${name}: ${value}`);
            motion.easing[key] = b.slice(1).map(Number);
        }
    } else if ((m = name.match(/^--duration(-[a-z]+)?$/))) {
        motion.duration[m[1] ? camel(m[1]) : 'standard'] = toMs(name, value);
    } else if (name === '--stagger') {
        motion.stagger = toMs(name, value);
    } else if (name === '--toast-hold') {
        motion.toastHold = toMs(name, value);
    } else {
        fail(`unmapped custom property ${name}: upstream added a token; map or ignore it in scripts/gen-tokens.mjs.`);
    }
}

for (const [n, v] of lightDecl) classify(n, v, 'light');
for (const [n, v] of darkDecl) classify(n, v, 'dark');

// Light and dark must declare the same themed roles (upstream's own rule).
for (const [n] of darkDecl) {
    if (SHARED_COLOR.test(n)) fail(`${n} is a shared color but the dark block redeclares it.`);
    if (IGNORED.has(n)) continue;
    if (!lightDecl.has(n)) fail(`${n} is declared in dark but not in light.`);
}
for (const [n] of lightDecl) {
    if (SHARED_COLOR.test(n) || IGNORED.has(n)) continue;
    if (!(COLOR_ROLE.test(n) || SHADOW.test(n))) continue;
    if (/^--(radius|font|fs|duration|ease|sidebar|content|stagger|toast)/.test(n)) continue;
    if (!darkDecl.has(n)) fail(`${n} is declared in light but not in dark.`);
}
// Shared colors apply in both themes.
for (const [n, v] of lightDecl) if (SHARED_COLOR.test(n)) colors.dark[camel(n)] = v;

// Palettes, merged over the base.
const pBlocks = blocks(palettesCss);
const palettes = {};
for (const b of pBlocks) {
    const m = b.selector.match(/^:root\[data-palette="([a-z0-9-]+)"\](\[data-theme="dark"\])?$/);
    if (!m) fail(`palettes.css has an unrecognized selector: ${b.selector}`);
    const [, id, dark] = m;
    const theme = dark ? 'dark' : 'light';
    palettes[id] ??= {};
    if (palettes[id][theme]) fail(`palette ${id} declares ${theme} twice.`);
    const merged = { ...colors[theme] };
    for (const [n, v] of declarations(b.body)) {
        const key = camel(n);
        if (!(key in colors[theme])) fail(`palette ${id} (${theme}) overrides ${n}, which the base does not declare.`);
        merged[key] = parseColor(n, v);
    }
    palettes[id][theme] = merged;
}
for (const [id, p] of Object.entries(palettes)) {
    if (!p.light || !p.dark) fail(`palette ${id} must declare both light and dark.`);
}

// Rules outside the token blocks.
function numberIn(selectorTest, prop, what, list = indexBlocks) {
    const b = list.find((x) => selectorTest(x.selector) && new RegExp(`(^|[;\\s])${prop}\\s*:`).test(x.body));
    const m = b && b.body.match(new RegExp(`(?:^|[;\\s])${prop}\\s*:\\s*([0-9.]+)(px)?\\s*(?:!important)?\\s*(?:;|$)`));
    if (!m) fail(`could not find ${what} in index.css; upstream changed the rule, update scripts/gen-tokens.mjs.`);
    return Number(m[1]);
}
const metrics = {
    lineHeight: numberIn((s) => s === 'html, body, #root', 'line-height', 'the base line height'),
    lineHeightAr: numberIn((s) => s === ':root[lang="ar"] #root', 'line-height', "Arabic's line height"),
    lineHeightArProse: numberIn(
        (s) => s.includes('.composer-input:dir(rtl)'),
        'line-height',
        "Arabic prose's line height",
    ),
    webTouchTarget: numberIn((s) => s === '.btn-icon', 'width', 'the 44px touch target (.btn-icon)'),
    inputFontSize: numberIn((s) => s === 'input, textarea, select', 'font-size', 'the 16px input text'),
};
if (metrics.webTouchTarget !== 44) {
    // The first `.btn-icon` block is the 32px desktop size; the phone size is the media-query one.
    const phone = indexBlocks.filter((x) => x.selector === '.btn-icon' && /width\s*:\s*44px/.test(x.body));
    if (!phone.length) fail('could not find the 44px phone touch target in index.css.');
    metrics.webTouchTarget = 44;
}

const stable = (o) =>
    JSON.stringify(o, null, 4)
        .replace(/"([A-Za-z_][A-Za-z0-9_]*)":/g, '$1:')
        .replace(/"/g, "'");
const out = `// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Generated by scripts/gen-tokens.mjs from ${SOURCES.join(' and ')}. Do not edit; run \`npm run gen:tokens\`.

export const colors = ${stable(colors)} as const;

export const palettes = ${stable(palettes)} as const;

export const shadows = ${stable(shadows)} as const;

export const radius = ${stable(radius)} as const;

export const fontSizes = ${stable(type)} as const;

export const faces = ${stable(faces)} as const;

export const layout = ${stable(layout)} as const;

export const motion = ${stable(motion)} as const;

export const metrics = ${stable(metrics)} as const;

export type ColorRole = keyof typeof colors.light;
export type PaletteId = keyof typeof palettes;
`;

if (process.argv.includes('--check')) {
    let current = '';
    try {
        current = readFileSync(outFile, 'utf8');
    } catch {
        // a missing file is stale too
    }
    if (current !== out) {
        console.error("tokens are stale: upstream's CSS changed; run `npm run gen:tokens`");
        process.exit(1);
    }
    console.log('tokens are current');
} else {
    mkdirSync(dirname(outFile), { recursive: true });
    writeFileSync(outFile, out);
    console.log(`wrote ${outFile}`);
}
