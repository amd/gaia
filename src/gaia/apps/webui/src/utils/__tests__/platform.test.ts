// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, describe, expect, it, vi } from 'vitest';
import { formatShortcut, isMacPlatform } from '../platform';

afterEach(() => vi.restoreAllMocks());

describe('formatShortcut', () => {
    it('uses macOS symbols in menu order on a Mac', () => {
        expect(formatShortcut('Mod+Shift+O', true)).toBe('⇧⌘O');
        expect(formatShortcut('Mod+K', true)).toBe('⌘K');
        expect(formatShortcut('Mod+,', true)).toBe('⌘,');
        expect(formatShortcut('Shift+Enter', true)).toBe('⇧Enter');
        expect(formatShortcut('Esc', true)).toBe('Esc');
    });

    it('spells out Ctrl elsewhere', () => {
        expect(formatShortcut('Mod+Shift+O', false)).toBe('Ctrl+Shift+O');
        expect(formatShortcut('Mod+,', false)).toBe('Ctrl+,');
        expect(formatShortcut('Shift+Enter', false)).toBe('Shift+Enter');
    });

    it('detects the platform from the browser', () => {
        vi.spyOn(navigator, 'platform', 'get').mockReturnValue('MacIntel');
        expect(isMacPlatform()).toBe(true);
        expect(formatShortcut('Mod+B')).toBe('⌘B');
        vi.spyOn(navigator, 'platform', 'get').mockReturnValue('Win32');
        expect(isMacPlatform()).toBe(false);
        expect(formatShortcut('Mod+B')).toBe('Ctrl+B');
    });
});
