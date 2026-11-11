// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export type Theme = 'light' | 'dark';

/** What the user picked; `system` follows the OS appearance. */
export type ThemePreference = Theme | 'system';

/** The OS appearance, or dark (the designed default) where it can't be read. */
export function systemTheme(): Theme {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'dark';
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export function resolveTheme(pref: ThemePreference): Theme {
    return pref === 'system' ? systemTheme() : pref;
}

/**
 * The canvas, spelled out. `<meta name="theme-color">` is what paints the
 * browser frame around the app on mobile Chrome, an installed PWA, and some
 * Linux window managers, and a meta tag has no cascade to read `--bg-primary`
 * from — so the two values live here and `contrast.test.ts` holds them to that
 * token in both themes.
 */
const CANVAS: Record<Theme, string> = {
    light: '#FAF9F7',
    dark: '#1A1917',
};

/** Single writer for the theme, so the frame can never disagree with the app. */
export function applyTheme(theme: Theme): void {
    document.documentElement.setAttribute('data-theme', theme);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
        // Not thrown: the app is already themed by the line above, and blanking
        // it over a frame colour is the worse failure.
        console.error(
            'applyTheme: index.html declares no <meta name="theme-color">, so the ' +
                'browser frame keeps its default colour instead of the app canvas. ' +
                'Restore the tag in src/gaia/apps/webui/index.html.',
        );
        return;
    }
    meta.setAttribute('content', CANVAS[theme]);
}

/**
 * On Windows and macOS the desktop app has no system title bar, so the top rows
 * stand in for it. Linux keeps the system frame and gets none of that CSS.
 */
export function markTitleBar(): void {
    const kind = window.gaiaWindow?.titleBar;
    if (kind && kind !== 'native') document.documentElement.dataset.titlebar = kind;
}

/** Every backdrop that dims the whole window, the strip under the window controls included. */
const SCRIMS = '.settings-overlay, .modal-overlay, .model-needed-overlay, .confirm-overlay, .version-picker-overlay, .cp-overlay';

const scrims = (): Element[] => [...document.querySelectorAll(SCRIMS)];

/** `[r, g, b, alpha]` of a hex or rgb() color, in the forms the build and the browser write. */
function channels(color: string): [number, number, number, number] {
    const value = color.trim();
    const hex = value.match(/^#([\da-f]{3,4}|[\da-f]{6}|[\da-f]{8})$/i)?.[1];
    if (hex) {
        const pairs = hex.length <= 4 ? [...hex].map((d) => d + d) : hex.match(/../g)!;
        const [r, g, b, a = 'ff'] = pairs;
        return [parseInt(r, 16), parseInt(g, 16), parseInt(b, 16), parseInt(a, 16) / 255];
    }
    const fn = value.match(/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:\s*[,/]\s*([\d.]+)(%?))?\s*\)$/i);
    if (fn) {
        const alpha = fn[4] === undefined ? 1 : Number(fn[4]) / (fn[5] ? 100 : 1);
        return [Number(fn[1]), Number(fn[2]), Number(fn[3]), alpha];
    }
    throw new Error(`Title bar: cannot read the color "${color}"; use a hex or rgb() value in src/styles/index.css.`);
}

/** `base` seen through each scrim in turn, as the `#rrggbb` the native window controls accept. */
export function colorUnderScrims(base: string, scrimColors: string[]): string {
    let [r, g, b] = channels(base);
    for (const scrim of scrimColors) {
        const [sr, sg, sb, alpha] = channels(scrim);
        [r, g, b] = [r + (sr - r) * alpha, g + (sg - g) * alpha, b + (sb - b) * alpha];
    }
    return `#${[r, g, b].map((c) => Math.round(c).toString(16).padStart(2, '0')).join('')}`;
}

/** Repaint the window controls as a modal opens and closes. */
export function watchTitleBarScrim(): void {
    if (window.gaiaWindow?.titleBar !== 'overlay') return;
    let open = scrims().length;
    new MutationObserver(() => {
        if (scrims().length === open) return;
        open = scrims().length;
        syncTitleBar();
    }).observe(document.body, { childList: true, subtree: true });
}

/**
 * Paint the desktop app's window controls and the browser frame from the live
 * canvas and text tokens, so a palette reaches them too. applyPalette calls it,
 * and every theme change is followed by applyPalette, so the colors go once.
 */
export function syncTitleBar(): void {
    const root = document.documentElement;
    const tokens = getComputedStyle(root);
    const canvas = tokens.getPropertyValue('--bg-primary').trim();
    // Right to left, the controls sit over the sidebar instead of the canvas.
    const behind = root.dir === 'rtl' ? tokens.getPropertyValue('--bg-sidebar').trim() : canvas;
    const text = tokens.getPropertyValue('--text-primary').trim();
    if (window.gaiaWindow?.titleBar === 'overlay' && behind && text) {
        const dims = scrims().map((el) => getComputedStyle(el).backgroundColor);
        const colors = { color: colorUnderScrims(behind, dims), symbolColor: colorUnderScrims(text, dims) };
        window.gaiaWindow.setColors(colors).catch((err: unknown) => {
            console.error('syncTitleBar: the desktop window controls did not take the colors', err);
        });
    }
    if (root.dataset.palette && canvas) {
        document.querySelector('meta[name="theme-color"]')?.setAttribute('content', canvas);
    }
}
