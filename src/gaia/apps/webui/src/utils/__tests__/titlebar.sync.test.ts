// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatStore } from '../../stores/chatStore';

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
        expect(setColors).toHaveBeenCalledWith({ color: '#101010', symbolColor: '#EEEEEE' });
    });

    it('get the colors when the palette changes', () => {
        useChatStore.getState().setPalette('stone');
        expect(setColors).toHaveBeenCalledTimes(1);
    });
});
