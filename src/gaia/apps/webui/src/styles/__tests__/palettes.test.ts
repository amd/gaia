// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Every color palette (palettes.css) and every custom color pick stays readable:
 * text clears 4.5:1 on the surfaces it sits on, the accent reads as text on
 * the canvas and inside its own tinted buttons, and rules stay visible.
 */

import { describe, expect, it } from 'vitest';
import { PALETTES, contrast, deriveCustomTokens, type CustomColors } from '../../utils/palette';

const PALETTES_CSS = Object.values(
    import.meta.glob('/src/styles/palettes.css', { query: '?raw', import: 'default', eager: true }),
)[0] as string;
const INDEX_CSS = Object.values(
    import.meta.glob('/src/styles/index.css', { query: '?raw', import: 'default', eager: true }),
)[0] as string;

/** `{ --token: value }` declared inside the rule whose selector is exactly `selector`. */
function block(css: string, selector: string): Record<string, string> {
    const at = css.indexOf(`${selector} {`);
    if (at < 0) throw new Error(`palettes.css has no "${selector}" block`);
    const body = css.slice(css.indexOf('{', at) + 1, css.indexOf('}', at));
    return Object.fromEntries([...body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
}

type Tokens = Record<string, string>;

function readabilityProblems(t: Tokens): string[] {
    const problems: string[] = [];
    const need = (fg: string, bg: string, floor: number) => {
        const ratio = contrast(t[fg], t[bg]);
        if (ratio < floor) problems.push(`${fg} on ${bg}: ${ratio.toFixed(2)} < ${floor}`);
    };
    const surfaces = ['--bg-primary', '--bg-secondary', '--bg-tertiary', '--bg-sidebar', '--bg-card', '--bg-hover', '--bg-active', '--bg-recessed'];
    for (const bg of surfaces) {
        need('--text-primary', bg, 4.5);
        need('--text-secondary', bg, 4.5);
        need('--text-muted', bg, 4.5);
    }
    for (const bg of ['--bg-primary', '--bg-card', '--bg-sidebar']) {
        need('--accent', bg, 4.5);
        need('--border', bg, 1.5);
    }
    need('--accent-fill-text', '--accent-fill', 4.5);
    need('--accent-fill-text', '--accent-fill-hover', 4.5);
    need('--accent-solid', '--bg-primary', 3);
    return problems;
}

const PALETTE_IDS = PALETTES.map((p) => p.id).filter((id) => id !== 'default');

describe('built-in palettes', () => {
    it('defines a light and a dark block for every palette the picker offers', () => {
        for (const id of PALETTE_IDS) {
            expect(() => block(PALETTES_CSS, `:root[data-palette="${id}"]`), id).not.toThrow();
            expect(() => block(PALETTES_CSS, `:root[data-palette="${id}"][data-theme="dark"]`), id).not.toThrow();
        }
    });

    it.each(PALETTE_IDS.flatMap((id) => [[id, 'light'], [id, 'dark']]))('keeps %s readable in %s', (id, theme) => {
        const sel = theme === 'dark' ? `:root[data-palette="${id}"][data-theme="dark"]` : `:root[data-palette="${id}"]`;
        expect(readabilityProblems(block(PALETTES_CSS, sel))).toEqual([]);
    });

    it('re-tints only tokens index.css declares', () => {
        const declared = new Set([...INDEX_CSS.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
        const unknown = [...PALETTES_CSS.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]).filter((t) => !declared.has(t));
        expect([...new Set(unknown)]).toEqual([]);
    });
});

describe('custom colors', () => {
    // Deliberately awkward picks: near-white and near-black accents, saturated
    // primaries, and tints that fight the accent.
    const PICKS: CustomColors[] = [
        { accent: '#FFFFFF', base: '#000000' },
        { accent: '#000000', base: '#FFFFFF' },
        { accent: '#FF0000', base: '#00FF00' },
        { accent: '#FFFF00', base: '#0000FF' },
        { accent: '#1F5FA8', base: '#6B7A8C' },
        { accent: '#E91E63', base: '#795548' },
        { accent: '#00BCD4', base: '#FF9800' },
        { accent: '#808080', base: '#808080' },
    ];

    it.each(PICKS.flatMap((c) => [[c, 'light'], [c, 'dark']] as const))('stays readable for %o in %s', (colors, theme) => {
        expect(readabilityProblems(deriveCustomTokens(colors, theme))).toEqual([]);
    });
});
