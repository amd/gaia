// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A chat title orders its own text but takes its side, padding and fade from
// the row. With dir="auto" a Latin title in an Arabic sidebar got the left-side
// fade over its first letter ("eval q01" read "val q01"). jsdom does no layout,
// so this reads the shipped stylesheet and the row markup.

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
    it('lets the text pick its order without changing the element direction', () => {
        expect(rule(sidebarCss, '.sb-row-title')).toMatch(/unicode-bidi:\s*plaintext/);
        expect(sidebarSource).not.toMatch(/className="sb-row-title"[^>]*\bdir=/);
    });

    it('aligns every title to the right, and fades on the left, in a right-to-left row', () => {
        const rtl = rule(sidebarCss, '.sb-row-title:dir(rtl)');
        expect(rtl).toMatch(/text-align:\s*right/);
        expect(rtl).toMatch(/mask-image:\s*linear-gradient\(to left/);
    });
});
