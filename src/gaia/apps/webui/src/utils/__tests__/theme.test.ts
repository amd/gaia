// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * `applyTheme` is the single writer for the theme, so nothing else in the app
 * can disagree with it -- which also means nothing else fails when it is wrong.
 * The sibling guard in styles/__tests__/contrast.test.ts scrapes this module as
 * text to pin its two hex constants to `--bg-primary`; it never calls it, so
 * rewriting the body to always write `light` used to pass the whole suite.
 *
 * The expected values are parsed from index.css rather than written out here,
 * so the write and the pin cannot drift apart.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { applyTheme } from '../theme';

const INDEX_CSS = (
    import.meta.glob('/src/styles/index.css', {
        query: '?raw',
        import: 'default',
        eager: true,
    }) as Record<string, string>
)['/src/styles/index.css'];

/** `--bg-primary` out of one top-level rule of index.css. */
function canvasOf(selector: string): string {
    const at = INDEX_CSS.indexOf(selector + ' {');
    if (at < 0) throw new Error(`index.css has no "${selector}" block`);
    const body = INDEX_CSS.slice(at, INDEX_CSS.indexOf('\n}', at));
    const m = body.match(/^\s*--bg-primary\s*:\s*(#[0-9a-fA-F]{3,8})\s*;/m);
    if (!m) throw new Error(`"${selector}" declares no --bg-primary`);
    return m[1].toLowerCase();
}

const CANVAS = {
    light: canvasOf(':root'),
    dark: canvasOf('[data-theme="dark"]'),
} as const;

const meta = () => document.querySelector('meta[name="theme-color"]');

describe('applyTheme', () => {
    beforeEach(() => {
        document.documentElement.removeAttribute('data-theme');
        meta()?.remove();
        const tag = document.createElement('meta');
        tag.setAttribute('name', 'theme-color');
        tag.setAttribute('content', '#000000');
        document.head.appendChild(tag);
    });

    afterEach(() => {
        vi.restoreAllMocks();
    });

    it('reads index.css, not an empty set', () => {
        expect(INDEX_CSS, 'the /src/styles/index.css glob picked up nothing').toBeTruthy();
        expect(CANVAS.light).not.toBe(CANVAS.dark);
    });

    it.each(['light', 'dark'] as const)('%s: marks the document with the theme', (theme) => {
        applyTheme(theme);
        expect(
            document.documentElement.getAttribute('data-theme'),
            'the cascade selects the palette off this attribute',
        ).toBe(theme);
    });

    it.each(['light', 'dark'] as const)('%s: repaints the browser frame', (theme) => {
        applyTheme(theme);
        expect(
            meta()?.getAttribute('content')?.toLowerCase(),
            'the frame no longer matches --bg-primary for this theme',
        ).toBe(CANVAS[theme]);
    });

    it('repaints the frame again when the theme is toggled back', () => {
        applyTheme('light');
        applyTheme('dark');
        expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
        expect(meta()?.getAttribute('content')?.toLowerCase()).toBe(CANVAS.dark);
    });

    it('still themes the app, and says so, when the meta tag is missing', () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => {});
        meta()?.remove();

        applyTheme('light');

        expect(document.documentElement.getAttribute('data-theme')).toBe('light');
        expect(error, 'a missing frame colour must not pass silently').toHaveBeenCalledTimes(1);
        expect(String(error.mock.calls[0][0]), 'the message must name the file to fix').toContain(
            'index.html',
        );
    });
});
