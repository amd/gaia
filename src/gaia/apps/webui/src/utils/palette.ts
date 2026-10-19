// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Theme } from './theme';
import { syncTitleBar } from './theme';

/** The default look (index.css), a palette from palettes.css, or the user's own colors. */
export type PaletteId =
    | 'default' | 'stone' | 'marble' | 'glacier' | 'forest' | 'sage' | 'basalt' | 'gold' | 'dusk' | 'custom';

export interface PaletteInfo {
    id: PaletteId;
    name: string;
    /** Canvas and accent in light, accent in dark: enough to recognize it in a picker. */
    swatch: [string, string, string];
}

export const PALETTES: PaletteInfo[] = [
    { id: 'default', name: 'Warm gray', swatch: ['#FAF9F7', '#B08D57', '#D4B47E'] },
    { id: 'stone', name: 'Stone and ink', swatch: ['#FAFAF9', '#2B2B29', '#E4E4E0'] },
    { id: 'marble', name: 'Blue marble', swatch: ['#F8FAFC', '#1F5FA8', '#5B9BE6'] },
    { id: 'glacier', name: 'Glacier', swatch: ['#F7FAFA', '#2C6E78', '#5FB4BF'] },
    { id: 'forest', name: 'Forest floor', swatch: ['#F8F9F7', '#3A6B48', '#7DBA8C'] },
    { id: 'sage', name: 'Sage and mist', swatch: ['#F9F9F7', '#5E7A63', '#9BB5A0'] },
    { id: 'basalt', name: 'Basalt and lichen', swatch: ['#F8F8F6', '#6B7A2E', '#B4C36A'] },
    { id: 'gold', name: 'Graphite and gold', swatch: ['#F7F7F8', '#D9A300', '#F2C230'] },
    { id: 'dusk', name: 'Dusk', swatch: ['#F9F9FB', '#4F5A9C', '#97A1E0'] },
];

export const PALETTE_IDS = new Set<string>([...PALETTES.map((p) => p.id), 'custom']);

/** Two colors the user picks; every other token is derived from them. */
export interface CustomColors {
    /** Buttons, links, focus and the active chat. */
    accent: string;
    /** The tint the grays lean toward. */
    base: string;
}

export const DEFAULT_CUSTOM: CustomColors = { accent: '#1F5FA8', base: '#6B7A8C' };

const HEX = /^#[0-9a-fA-F]{6}$/;
export const isHexColor = (v: unknown): v is string => typeof v === 'string' && HEX.test(v);

type RGB = [number, number, number];
const rgb = (h: string): RGB => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB;
const hex = (c: RGB) => '#' + c.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('').toUpperCase();
/** `t` of `b` mixed into `a`. */
export const mix = (a: string, b: string, t: number) => { const x = rgb(a), y = rgb(b); return hex([0, 1, 2].map((i) => x[i] + (y[i] - x[i]) * t) as RGB); };
const channel = (v: number) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
const luminance = (h: string) => { const [r, g, b] = rgb(h).map(channel); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
export function contrast(a: string, b: string): number {
    const [hi, lo] = [luminance(a), luminance(b)].sort((p, q) => q - p);
    return (hi + 0.05) / (lo + 0.05);
}
/** Move `fg` toward `toward` until it clears `target` against every background. */
function ensure(fg: string, bgs: string[], target: number, toward: string): string {
    let out = fg;
    for (let t = 0.02; Math.min(...bgs.map((b) => contrast(out, b))) < target && t <= 1; t += 0.02) out = mix(fg, toward, t);
    return out;
}
const rgba = (h: string, a: number) => { const [r, g, b] = rgb(h); return `rgba(${r}, ${g}, ${b}, ${a})`; };

/** The tokens palettes.css sets, derived from two colors with text held to 4.5:1. */
export function deriveCustomTokens(c: CustomColors, theme: Theme): Record<string, string> {
    const dark = theme === 'dark';
    const far = dark ? '#FFFFFF' : '#000000';
    const bg = dark ? mix('#151515', c.base, 0.1) : mix('#FAFAFA', c.base, 0.06);
    const side = dark ? mix('#111111', c.base, 0.1) : mix('#F1F1F1', c.base, 0.1);
    const text = dark ? mix('#ECECEC', c.base, 0.06) : mix('#1B1B1B', c.base, 0.12);
    const card = dark ? mix(bg, text, 0.06) : '#FFFFFF';
    const hover = mix(bg, text, dark ? 0.09 : 0.06);
    const active = mix(bg, text, dark ? 0.14 : 0.1);
    const tertiary = mix(bg, text, dark ? 0.11 : 0.07);
    const secondary = mix(bg, side, 0.5);
    const recessed = dark ? mix(bg, '#000000', 0.15) : mix(bg, side, 0.8);
    const surfaces = [bg, side, card, hover, secondary, tertiary, recessed, active];
    const muted = ensure(dark ? mix('#A3A3A3', c.base, 0.12) : mix('#5E5E5E', c.base, 0.18), surfaces, 4.6, far);
    const border = ensure(mix(bg, text, dark ? 0.24 : 0.26), [bg, card, side], 1.6, far);
    // A dark accent on a dark canvas, or a pale one on a light canvas, is lifted until it reads.
    const solid = ensure(c.accent, [bg], 3, far);
    const accentText = ensure(solid, [bg, card, side], 4.6, far);
    const fill = mix(card, solid, dark ? 0.22 : 0.14);
    const fillHover = mix(card, solid, dark ? 0.3 : 0.22);
    return {
        '--bg-primary': bg, '--bg-secondary': secondary, '--bg-tertiary': tertiary,
        '--bg-sidebar': side, '--bg-input': card, '--bg-card': card, '--bg-hover': hover,
        '--bg-active': active, '--bg-user-msg': card, '--bg-recessed': recessed,
        '--text-primary': text, '--text-secondary': muted, '--text-muted': muted,
        '--border': border, '--border-light': border, '--border-recessed': border,
        '--accent': accentText, '--accent-light': mix(solid, '#FFFFFF', 0.18), '--accent-solid': solid,
        '--accent-dim': rgba(solid, 0.16), '--accent-dim2': rgba(solid, 0.08),
        '--accent-selection': rgba(solid, 0.26), '--accent-ring': rgba(solid, 0.38),
        '--accent-fill': fill, '--accent-fill-text': ensure(accentText, [fill, fillHover], 4.6, far),
        '--accent-fill-hover': fillHover,
        '--tint-subtle': rgba(text, 0.03), '--tint-hover': rgba(text, dark ? 0.06 : 0.05),
        ...(dark ? { '--text-inverse': bg, '--switch-knob': text } : {}),
    };
}

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
