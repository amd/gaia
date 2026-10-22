// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export function isMacPlatform(): boolean {
    return typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform);
}

const MAC_SYMBOLS: Record<string, string> = { Ctrl: '⌃', Alt: '⌥', Shift: '⇧', Mod: '⌘' };
// macOS menus list modifiers in this order.
const MAC_ORDER = ['Ctrl', 'Alt', 'Shift', 'Mod'];

/**
 * Label a shortcut written as `Mod+Shift+O` for this platform: `⇧⌘O` on
 * macOS, `Ctrl+Shift+O` elsewhere. `Mod` is ⌘ on macOS and Ctrl elsewhere,
 * matching handlers that accept `e.ctrlKey || e.metaKey`.
 */
export function formatShortcut(shortcut: string, mac: boolean = isMacPlatform()): string {
    const mods = shortcut.split('+');
    const key = mods.pop() as string;
    if (!mac) return [...mods.map((m) => (m === 'Mod' ? 'Ctrl' : m)), key].join('+');
    const ordered = MAC_ORDER.filter((m) => mods.includes(m));
    return ordered.map((m) => MAC_SYMBOLS[m]).join('') + key;
}
