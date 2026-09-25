// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export type Theme = 'light' | 'dark';

/**
 * The canvas, spelled out. `<meta name="theme-color">` is what paints the
 * browser frame around the app on mobile Chrome, an installed PWA, and some
 * Linux window managers, and a meta tag has no cascade to read `--bg-primary`
 * from — so the two values live here and `contrast.test.ts` holds them to that
 * token in both themes.
 */
const CANVAS: Record<Theme, string> = {
    light: '#F5F2EC',
    dark: '#17161C',
};

/** Single writer for the theme, so the frame can never disagree with the app. */
export function applyTheme(theme: Theme): void {
    document.documentElement.setAttribute('data-theme', theme);
    document
        .querySelector('meta[name="theme-color"]')
        ?.setAttribute('content', CANVAS[theme]);
}
