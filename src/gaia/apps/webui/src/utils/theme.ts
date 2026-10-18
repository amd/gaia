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
 * Paint the desktop app's window controls and the browser frame from the live
 * canvas and text tokens, so a palette reaches them too. applyPalette calls it,
 * and every theme change is followed by applyPalette, so the colors go once.
 */
export function syncTitleBar(): void {
    const root = document.documentElement;
    const tokens = getComputedStyle(root);
    const color = tokens.getPropertyValue('--bg-primary').trim();
    const symbolColor = tokens.getPropertyValue('--text-primary').trim();
    if (window.gaiaWindow && color && symbolColor) {
        window.gaiaWindow.setColors({ color, symbolColor }).catch((err: unknown) => {
            console.error('syncTitleBar: the desktop window controls did not take the colors', err);
        });
    }
    if (root.dataset.palette && color) {
        document.querySelector('meta[name="theme-color"]')?.setAttribute('content', color);
    }
}
