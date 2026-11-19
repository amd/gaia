// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A chat title orders its own text (dir="auto") but takes its side from the row.
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

function rule(css: string, selector: string): string {
    const bare = css.replace(/\/\*[\s\S]*?\*\//g, '');
    const escaped = selector.replace(/[.()]/g, (c) => `\\${c}`);
    const re = new RegExp(String.raw`(?:^|\})\s*` + escaped + String.raw`\s*\{([^}]*)\}`);
    return bare.match(re)?.[1] ?? '';
}

describe('sidebar title direction', () => {
    it('gives each title its own text order, so the ellipsis ends its own text', () => {
        expect(sidebarSource).toMatch(/className="sb-row-title" dir="auto"/);
        expect(rule(sidebarCss, '.sb-row-title')).not.toMatch(/unicode-bidi/);
    });

    it('takes the side from the row: right in a right-to-left row, left in a left-to-right one', () => {
        expect(rule(sidebarCss, '.sb-row-main:dir(rtl) .sb-row-title')).toMatch(/text-align:\s*right/);
        expect(rule(sidebarCss, '.sb-row-main:dir(ltr) .sb-row-title')).toMatch(/text-align:\s*left/);
    });

    it('fills the row and cuts a long title with an ellipsis, not a fade fixed to one side', () => {
        const base = rule(sidebarCss, '.sb-row-title');
        expect(base).toMatch(/text-overflow:\s*ellipsis/);
        expect(base).toMatch(/flex:\s*1/);
        expect(base).not.toMatch(/mask-image/);
    });
});
