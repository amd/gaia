// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * GAIA's motion is one system (#5259): every animation and transition takes
 * its duration and easing from the tokens in index.css, and the reduced-motion
 * rule stops all of it. jsdom runs no animations, so this reads the stylesheets.
 */

import { describe, expect, it } from 'vitest';

const CSS_SOURCES = import.meta.glob('/src/**/*.css', {
    query: '?raw',
    import: 'default',
    eager: true,
}) as Record<string, string>;

const REDUCED_QUERY = '@media (prefers-reduced-motion: reduce)';

function stripComments(css: string): string {
    return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/** [start, end) of every `@media (prefers-reduced-motion: reduce)` block. */
function reducedRanges(css: string): Array<[number, number]> {
    const ranges: Array<[number, number]> = [];
    let from = 0;
    for (;;) {
        const at = css.indexOf(REDUCED_QUERY, from);
        if (at < 0) break;
        let depth = 0;
        let i = css.indexOf('{', at);
        do {
            if (css[i] === '{') depth++;
            else if (css[i] === '}') depth--;
            i++;
        } while (depth > 0 && i < css.length);
        ranges.push([at, i]);
        from = i;
    }
    return ranges;
}

const MOTION_TOKEN = /^--(?:duration|ease|stagger|toast-hold)\b/;

/** Animation/transition timings or easings that don't come from a motion token, outside reduced-motion blocks. */
function hardcodedMotion(): string[] {
    const found: string[] = [];
    const decl = /((?:animation|transition)(?:-[a-z-]+)?)\s*:\s*([^;{}]+)(?=[;}])/g;
    for (const [file, raw] of Object.entries(CSS_SOURCES)) {
        const css = stripComments(raw);
        const skip = reducedRanges(css);
        for (const m of css.matchAll(decl)) {
            if (skip.some(([a, b]) => m.index! >= a && m.index! < b)) continue;
            // A fallback or a non-motion variable is a hidden literal.
            const foreign = [...m[2].matchAll(/var\(\s*(--[\w-]+)\s*(,)?/g)]
                .some(([, name, fallback]) => fallback || !MOTION_TOKEN.test(name));
            const value = m[2].replace(/var\([^)]*\)/g, '');
            const time = /(?<![\w-])(?!0s\b)\d*\.?\d+m?s\b/.test(value);
            const easing = /(?<![\w-])(ease(-in|-out|-in-out)?|linear|cubic-bezier|steps)(?![\w-])/.test(value);
            if (foreign || time || easing) found.push(`${file}: ${m[1]}: ${m[2].trim()}`);
        }
    }
    return found;
}

describe('motion system', () => {
    it('takes every duration and easing from the motion tokens', () => {
        expect(hardcodedMotion()).toEqual([]);
    });

    it('defines every motion token it uses', () => {
        const index = CSS_SOURCES['/src/styles/index.css'];
        const used = new Set<string>();
        for (const raw of Object.values(CSS_SOURCES)) {
            for (const m of stripComments(raw).matchAll(/var\((--(?:duration|ease|stagger|toast-hold)[\w-]*)\)/g)) used.add(m[1]);
        }
        const missing = [...used].filter((name) => !new RegExp(`${name}\\s*:`).test(index));
        expect(missing).toEqual([]);
    });

    it('stops every animation and transition under reduced motion', () => {
        const css = stripComments(CSS_SOURCES['/src/styles/index.css']);
        const block = reducedRanges(css).map(([a, b]) => css.slice(a, b)).join('\n');
        expect(block).toMatch(/\*,\s*\*::before,\s*\*::after\s*\{/);
        expect(block).toMatch(/animation-duration:\s*0\.01ms\s*!important/);
        expect(block).toMatch(/animation-iteration-count:\s*1\s*!important/);
        expect(block).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
        expect(block).toMatch(/scroll-behavior:\s*auto\s*!important/);
    });

    it('drops the live status shimmer under reduced motion', () => {
        const css = stripComments(CSS_SOURCES['/src/styles/motion.css']);
        const block = reducedRanges(css).map(([a, b]) => css.slice(a, b)).join('\n');
        expect(block).toMatch(/\.thinking-indicator\.is-live \.thinking-indicator-text\s*\{[^}]*mask-image:\s*none/);
    });

    it('loops only on live state, never on an idle view', () => {
        const css = Object.values(CSS_SOURCES).map(stripComments).join('\n');
        const loops = [...css.matchAll(/([^{}]+)\{[^{}]*animation(?:-iteration-count)?:[^;}]*\binfinite\b[^}]*\}/g)]
            .map((m) => m[1].trim());
        // Each looping selector is drawn only while something is running.
        const live = /spin|spinner|is-live|cursor|thinking-dots|skeleton|starting|is-running/;
        expect(loops.filter((selector) => !live.test(selector))).toEqual([]);
    });
});
