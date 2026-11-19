// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatStore } from '../../stores/chatStore';
import { colorUnderScrims, markTitleBar } from '../theme';

describe('desktop window controls', () => {
    const setColors = vi.fn(() => Promise.resolve(true));
    // A stylesheet, not inline styles: applyPalette clears inline tokens.
    const sheet = document.createElement('style');
    sheet.textContent = ':root { --bg-primary: #101010; --text-primary: #EEEEEE; }';

    beforeEach(() => {
        setColors.mockClear();
        window.gaiaWindow = { titleBar: 'overlay', setColors };
        document.head.appendChild(sheet);
    });

    afterEach(() => {
        delete window.gaiaWindow;
        sheet.remove();
    });

    it('get the colors once per theme change, after the palette is applied', () => {
        useChatStore.getState().setThemePreference('light');
        expect(setColors).toHaveBeenCalledTimes(1);
        expect(setColors).toHaveBeenCalledWith({ color: '#101010', symbolColor: '#eeeeee' });
    });

    it('get the colors when the palette changes', () => {
        useChatStore.getState().setPalette('stone');
        expect(setColors).toHaveBeenCalledTimes(1);
    });

    it('are left alone where the system draws the frame (Linux)', () => {
        window.gaiaWindow = { titleBar: 'native', setColors };
        useChatStore.getState().setThemePreference('dark');
        useChatStore.getState().setPalette('stone');
        expect(setColors).not.toHaveBeenCalled();
    });
});

describe('desktop title-bar CSS', () => {
    afterEach(() => {
        delete window.gaiaWindow;
        delete document.documentElement.dataset.titlebar;
    });

    it.each(['overlay', 'mac'] as const)('turns on for the %s frame', (titleBar) => {
        window.gaiaWindow = { titleBar, setColors: vi.fn() };
        markTitleBar();
        expect(document.documentElement.dataset.titlebar).toBe(titleBar);
    });

    it('stays off under the Linux system frame', () => {
        window.gaiaWindow = { titleBar: 'native', setColors: vi.fn() };
        markTitleBar();
        expect(document.documentElement.dataset.titlebar).toBeUndefined();
    });

    it('stays off in a plain browser', () => {
        markTitleBar();
        expect(document.documentElement.dataset.titlebar).toBeUndefined();
    });
});

describe('window controls under a modal scrim', () => {
    it('read the scrim as the production build minifies it', () => {
        // The build writes rgba(31, 29, 26, 0.32) as #1f1d1a52.
        expect(colorUnderScrims('#faf9f7', ['#1f1d1a52'])).toBe('#b4b2b0');
        expect(colorUnderScrims('#FAF9F7', ['rgba(31, 29, 26, 0.32)'])).toBe('#b4b3b0');
    });

    it('read the browser computed and space-separated forms', () => {
        expect(colorUnderScrims('#ffffff', ['rgb(0 0 0 / 50%)'])).toBe('#808080');
        expect(colorUnderScrims('#fff', ['rgba(0, 0, 0, 0.5)'])).toBe('#808080');
    });

    it('stack two scrims and leave an opaque-free base as it is', () => {
        expect(colorUnderScrims('#ffffff', [])).toBe('#ffffff');
        expect(colorUnderScrims('#ffffff', ['rgba(0, 0, 0, 0.5)', 'rgba(0, 0, 0, 0.5)'])).toBe('#404040');
    });

    it('refuse a color it cannot read instead of sending one the window rejects', () => {
        expect(() => colorUnderScrims('#faf9f7', ['oklch(0.2 0 0 / 0.3)'])).toThrow(/cannot read the color/);
    });
});
