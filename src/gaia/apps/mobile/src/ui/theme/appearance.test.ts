// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { deriveCustomTokens } from '@webui/utils/palette.core';

import { colors, palettes } from '../tokens/generated';
import { DEFAULT_APPEARANCE, parseStored, resolveScheme, themeColors } from './appearance';
import { useAppearance } from './appearanceStore';

describe('appearance to theme', () => {
    afterEach(() => useAppearance.getState().reset());

    it('maps dark, a palette and a custom accent to the derived colors, and reset restores the defaults', () => {
        const { setScheme, setPalette, setCustom, reset } = useAppearance.getState();
        setScheme('dark');
        expect(resolveScheme(useAppearance.getState().scheme, 'light')).toBe('dark');

        setPalette('marble');
        let s = useAppearance.getState();
        expect(themeColors('dark', s.palette, s.custom)).toBe(palettes.marble.dark);

        const custom = { accent: '#C0392B', base: '#445566' };
        setCustom(custom);
        s = useAppearance.getState();
        expect(s.palette).toBe('custom');
        const derived = deriveCustomTokens(custom, 'dark');
        const got = themeColors('dark', s.palette, s.custom);
        expect(got.accentSolid).toBe(derived['--accent-solid']);
        expect(got.bgPrimary).toBe(derived['--bg-primary']);
        expect(got.textInverse).toBe(derived['--text-inverse']);
        expect(got.danger).toBe(colors.dark.danger);

        reset();
        s = useAppearance.getState();
        expect({ scheme: s.scheme, palette: s.palette, custom: s.custom, showResources: s.showResources }).toEqual(DEFAULT_APPEARANCE);
        expect(resolveScheme(s.scheme, 'light')).toBe('light');
    });

    it('ignores saved values it does not recognize', () => {
        expect(parseStored({ scheme: 'sepia', palette: 'nope', custom: { accent: 'red', base: '#112233' }, showResources: 'yes' })).toEqual({});
    });
});
