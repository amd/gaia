// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The cut-text fade is a mask held by an animation on a scroll timeline. A browser
// without scroll timelines runs that animation on the clock, so the mask stayed on
// text that fits (#8636). jsdom does no layout, so this reads the shipped stylesheets.

import { describe, expect, it } from 'vitest';

const SHEETS = import.meta.glob('/src/**/*.css', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

function bare(css: string): string {
    return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/** The body of the first block opened by *opener*, braces balanced. */
function block(css: string, opener: RegExp): string {
    const start = css.search(opener);
    if (start < 0) return '';
    const open = css.indexOf('{', start);
    let depth = 0;
    for (let i = open; i < css.length; i++) {
        if (css[i] === '{') depth++;
        if (css[i] === '}' && --depth === 0) return css.slice(open + 1, i);
    }
    return '';
}

describe('the cut-text fade', () => {
    const all = Object.entries(SHEETS).map(([path, css]) => [path, bare(css)] as const);

    it('defines its mask only where the browser has scroll timelines', () => {
        const index = bare(SHEETS['/src/styles/index.css']);
        const guarded = block(index, /@supports\s*\(animation-timeline:\s*scroll\(\)\)/);
        expect(guarded).toMatch(/@keyframes fade-cut-inline/);
        expect(guarded).toMatch(/@keyframes fade-cut-block/);

        const definitions = all.flatMap(([, css]) => css.match(/@keyframes fade-cut-(?:inline|block)/g) ?? []);
        expect(definitions).toHaveLength(2);
    });

    it('is never used without a scroll timeline', () => {
        const unbound: string[] = [];
        for (const [path, css] of all) {
            for (const rule of css.match(/[^{}]*\{[^{}]*\}/g) ?? []) {
                if (/animation:\s*fade-cut-/.test(rule) && !/animation-timeline:\s*scroll\(/.test(rule)) {
                    unbound.push(`${path}: ${rule.trim().split('{')[0].trim()}`);
                }
            }
        }
        expect(unbound).toEqual([]);
    });
});
