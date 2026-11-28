// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A chat title and snippet order their own text (dir="auto") but take their side from the row.
// A fade drawn on one physical side was wrong for a Latin title in an Arabic
// sidebar (#7628), so long titles end in an ellipsis. jsdom does no layout, so
// this reads the shipped stylesheet and the row markup.

import { describe, expect, it } from 'vitest';
const SOURCES = import.meta.glob(['/src/components/Sidebar.css', '/src/components/Sidebar.tsx'], {
    query: '?raw',
    import: 'default',
    eager: true,
}) as Record<string, string>;
const sidebarCss = SOURCES['/src/components/Sidebar.css'];
const sidebarSource = SOURCES['/src/components/Sidebar.tsx'];

// The declarations of the rule whose selector list names `selector`.
function rule(css: string, selector: string): string {
    const bare = css.replace(/\/\*[\s\S]*?\*\//g, '');
    for (const [, list, body] of bare.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
        if (list.split(',').some((s) => s.trim() === selector)) return body;
    }
    return '';
}

describe('sidebar title direction', () => {
    it('gives each title its own text order, so the ellipsis ends its own text', () => {
        expect(sidebarSource).toMatch(/className="sb-row-title" dir="auto"/);
        expect(rule(sidebarCss, '.sb-row-title')).not.toMatch(/unicode-bidi/);
    });

    it('takes the side from the row: right in a right-to-left row, left in a left-to-right one', () => {
        for (const part of ['.sb-row-title', '.sb-row-snippet']) {
            expect(rule(sidebarCss, `.sb-row-main:dir(rtl) ${part}`)).toMatch(/text-align:\s*right/);
            expect(rule(sidebarCss, `.sb-row-main:dir(ltr) ${part}`)).toMatch(/text-align:\s*left/);
        }
    });

    it('fills the row and fades a long title at its own end, never at a side fixed for every title', () => {
        const base = rule(sidebarCss, '.sb-row-title');
        expect(base).not.toMatch(/text-overflow:\s*ellipsis/);
        expect(base).toMatch(/animation:\s*fade-cut-inline/);
        expect(base).toMatch(/flex:\s*1/);
        // A mask written on the title itself would sit on one side whatever the title's direction.
        expect(base).not.toMatch(/mask-image/);
    });
});
