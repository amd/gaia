// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Theme } from './theme';
import { syncTitleBar } from './theme';
import { deriveCustomTokens, DEFAULT_CUSTOM, type CustomColors, type PaletteId } from './palette.core';

export * from './palette.core';

const CUSTOM_KEYS = Object.keys(deriveCustomTokens(DEFAULT_CUSTOM, 'dark'));

/** Single writer for the palette, after the theme: custom colors depend on light or dark. */
export function applyPalette(id: PaletteId, custom: CustomColors, theme: Theme): void {
    const root = document.documentElement;
    for (const key of CUSTOM_KEYS) root.style.removeProperty(key);
    if (id === 'default') delete root.dataset.palette;
    else root.dataset.palette = id;
    if (id === 'custom') {
        for (const [key, value] of Object.entries(deriveCustomTokens(custom, theme))) root.style.setProperty(key, value);
    }
    syncTitleBar();
}
