// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * At <= 768px the sidebar collapses to a floating "Open sidebar" button pinned
 * to the top-left of the window. Every row that can sit at the top of the main
 * area must start to the right of that button, or its first characters are
 * hidden ("Restored your last model." read as "ored your last model.").
 *
 * jsdom does no layout, so this reads the shipped stylesheets directly.
 */

import { describe, expect, it } from 'vitest';

const CSS_SOURCES = import.meta.glob('/src/**/*.css', {
    query: '?raw',
    import: 'default',
    eager: true,
}) as Record<string, string>;

const NARROW_QUERY = '@media (max-width: 768px)';

/** Rows that can be the first thing under the floating sidebar button. */
const TOP_ROWS = ['.app-notice', '.connection-banner', '.task-header', '.memory-dashboard-header'];

function stripComments(css: string): string {
    return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/** Bodies of every `@media (max-width: 768px)` block across the stylesheets. */
function narrowBlocks(): string[] {
    const blocks: string[] = [];
    for (const raw of Object.values(CSS_SOURCES)) {
        const css = stripComments(raw);
        let from = 0;
        for (;;) {
            const at = css.indexOf(NARROW_QUERY, from);
            if (at < 0) break;
            const open = css.indexOf('{', at);
            let depth = 1;
            let i = open + 1;
            while (depth > 0 && i < css.length) {
                if (css[i] === '{') depth++;
                else if (css[i] === '}') depth--;
                i++;
            }
            blocks.push(css.slice(open + 1, i - 1));
            from = i;
        }
    }
    return blocks;
}

/** Declarations of the top-level rules (outside any at-rule) for `selector`. */
function topLevelDecls(css: string, selector: string): string {
    const re = new RegExp(`(?:^|\\})\\s*${selector.replace('.', '\\.')}\\s*\\{([^}]*)\\}`, 'g');
    return [...stripComments(css).matchAll(re)].map((m) => m[1]).join(';');
}

function px(decls: string, prop: string): number {
    const m = decls.match(new RegExp(`(?:^|[;\\s])${prop}\\s*:\\s*(\\d+)px`));
    if (!m) throw new Error(`no ${prop} in px found in: ${decls}`);
    return Number(m[1]);
}

const indexCss = CSS_SOURCES['/src/styles/index.css'];

describe('sidebar toggle clearance at narrow widths', () => {
    it('reserves at least the floating button footprint', () => {
        const toggle = topLevelDecls(indexCss, '.sidebar-toggle');
        const footprint = px(toggle, 'left') + px(toggle, 'width');
        const clearance = indexCss.match(/--sidebar-toggle-clearance:\s*(\d+)px/);
        expect(clearance, '--sidebar-toggle-clearance is not defined').not.toBeNull();
        expect(Number(clearance![1])).toBeGreaterThan(footprint);
    });

    it.each(TOP_ROWS)('%s starts to the right of the button', (selector) => {
        const escaped = selector.replace('.', '\\.');
        const rule = new RegExp(`(?:^|[},])\\s*${escaped}\\s*\\{([^}]*)\\}`);
        const decls = narrowBlocks()
            .map((block) => block.match(rule)?.[1])
            .filter((d): d is string => d !== undefined)
            .join(';');
        expect(decls).toMatch(/padding-left:\s*var\(--sidebar-toggle-clearance\)/);
    });
});
