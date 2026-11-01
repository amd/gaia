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

const scrimOpen = () => document.querySelector('.modal-overlay, .settings-overlay') !== null;

/** `color` seen through the modal scrim, which can't reach the native window controls. */
function underScrim(color: string, scrim: string): string {
    const [r, g, b, alpha] = (scrim.match(/[\d.]+/g) ?? []).map(Number);
    const base = color.match(/^#([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i);
    if (!base || alpha === undefined) return color;
    const mix = (channel: number, over: number) =>
        Math.round(channel * (1 - alpha) + over * alpha).toString(16).padStart(2, '0');
    return `#${mix(parseInt(base[1], 16), r)}${mix(parseInt(base[2], 16), g)}${mix(parseInt(base[3], 16), b)}`;
}

/** Repaint the window controls as a modal opens and closes. */
export function watchTitleBarScrim(): void {
    if (window.gaiaWindow?.titleBar !== 'overlay') return;
    let open = scrimOpen();
    new MutationObserver(() => {
        if (scrimOpen() === open) return;
        open = !open;
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
    const color = scrimOpen() ? underScrim(behind, tokens.getPropertyValue('--bg-modal-overlay')) : behind;
    const symbolColor = tokens.getPropertyValue('--text-primary').trim();
    if (window.gaiaWindow?.titleBar === 'overlay' && color && symbolColor) {
        window.gaiaWindow.setColors({ color, symbolColor }).catch((err: unknown) => {
            console.error('syncTitleBar: the desktop window controls did not take the colors', err);
        });
    }
    if (root.dataset.palette && canvas) {
        document.querySelector('meta[name="theme-color"]')?.setAttribute('content', canvas);
    }
}
