// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { DEFAULT_CUSTOM, PALETTE_IDS, deriveCustomTokens, isHexColor, type CustomColors } from '@webui/utils/palette.core';

import { colors, palettes, type ColorRole, type PaletteId as GeneratedPaletteId } from '../tokens/generated';

export type SchemePreference = 'system' | 'light' | 'dark';
export type AppearancePaletteId = 'default' | 'custom' | GeneratedPaletteId;
export type Scheme = 'light' | 'dark';

export interface AppearanceState {
    scheme: SchemePreference;
    palette: AppearancePaletteId;
    custom: CustomColors;
    showResources: boolean;
}

export const DEFAULT_APPEARANCE: AppearanceState = {
    scheme: 'system',
    palette: 'default',
    custom: DEFAULT_CUSTOM,
    showResources: true,
};

export function resolveScheme(preference: SchemePreference, system: Scheme): Scheme {
    return preference === 'system' ? system : preference;
}

const camel = (cssVar: string) => cssVar.slice(2).replace(/-([a-z])/g, (_, c: string) => c.toUpperCase());

/** The roles a palette gives one scheme; custom layers the web's derived roles over the default look. */
export function themeColors(scheme: Scheme, palette: AppearancePaletteId, custom: CustomColors): Record<ColorRole, string> {
    if (palette === 'default') return colors[scheme];
    if (palette === 'custom') {
        const derived: Partial<Record<ColorRole, string>> = {};
        for (const [name, value] of Object.entries(deriveCustomTokens(custom, scheme))) derived[camel(name) as ColorRole] = value;
        return { ...colors[scheme], ...derived };
    }
    return palettes[palette][scheme];
}

/** Reads what the device saved; anything unrecognized falls back to that field's default, as the web does. */
export function parseStored(raw: unknown): Partial<AppearanceState> {
    if (!raw || typeof raw !== 'object') return {};
    const v = raw as Record<string, unknown>;
    const out: Partial<AppearanceState> = {};
    if (v.scheme === 'light' || v.scheme === 'dark' || v.scheme === 'system') out.scheme = v.scheme;
    if (typeof v.palette === 'string' && PALETTE_IDS.has(v.palette)) out.palette = v.palette as AppearancePaletteId;
    const c = v.custom as Partial<CustomColors> | undefined;
    if (c && isHexColor(c.accent) && isHexColor(c.base)) out.custom = { accent: c.accent, base: c.base };
    if (typeof v.showResources === 'boolean') out.showResources = v.showResources;
    return out;
}
