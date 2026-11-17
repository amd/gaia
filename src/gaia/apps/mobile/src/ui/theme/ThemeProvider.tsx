// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useColorScheme } from 'react-native';
import { radius, shadows, motion, faces } from '../tokens/generated';
import { space } from '../tokens/space';
import { typeScale } from '../tokens/type';
import { resolveScheme, themeColors, withDerivedColors, type Scheme, type ThemeColorRole } from './appearance';
import { useAppearance } from './appearanceStore';

export type { Scheme };

export interface Theme {
    scheme: Scheme;
    colors: Record<ThemeColorRole, string>;
    radius: typeof radius;
    space: typeof space;
    type: typeof typeScale;
    faces: typeof faces;
    shadows: Record<'sm' | 'md' | 'lg' | 'press', string>;
    motion: typeof motion;
}

const ThemeContext = createContext<Theme | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
    const system: Scheme = useColorScheme() === 'dark' ? 'dark' : 'light';
    const preference = useAppearance((s) => s.scheme);
    const palette = useAppearance((s) => s.palette);
    const custom = useAppearance((s) => s.custom);
    const scheme = resolveScheme(preference, system);
    const theme = useMemo<Theme>(
        () => ({
            scheme,
            colors: withDerivedColors(themeColors(scheme, palette, custom)),
            radius,
            space,
            type: typeScale,
            faces,
            shadows: shadows[scheme],
            motion,
        }),
        [scheme, palette, custom],
    );
    return <ThemeContext.Provider value={theme}>{children}</ThemeContext.Provider>;
}

export function useTheme(): Theme {
    const theme = useContext(ThemeContext);
    if (!theme)
        throw new Error('useTheme() needs a <UIProvider> above it. Mount UIProvider once in src/app/_layout.tsx.');
    return theme;
}
