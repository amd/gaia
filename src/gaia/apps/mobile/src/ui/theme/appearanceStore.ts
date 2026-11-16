// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';

import type { CustomColors } from '@webui/utils/palette.core';

import { DEFAULT_APPEARANCE, parseStored, type AppearancePaletteId, type AppearanceState, type SchemePreference } from './appearance';

const KEY = 'gaia.appearance';

interface AppearanceActions {
    setScheme(scheme: SchemePreference): void;
    setPalette(palette: AppearancePaletteId): void;
    /** Like the web, picking custom colors also selects the Custom palette. */
    setCustom(custom: CustomColors): void;
    setShowResources(show: boolean): void;
    /** Back to system, the default palette, the default custom pair and resource usage on. */
    reset(): void;
}

export const useAppearance = create<AppearanceState & AppearanceActions>((set) => ({
    ...DEFAULT_APPEARANCE,
    setScheme: (scheme) => set({ scheme }),
    setPalette: (palette) => set({ palette }),
    setCustom: (custom) => set({ custom, palette: 'custom' }),
    setShowResources: (showResources) => set({ showResources }),
    reset: () => set({ ...DEFAULT_APPEARANCE }),
}));

function persist(state: AppearanceState): void {
    const { scheme, palette, custom, showResources } = state;
    AsyncStorage.setItem(KEY, JSON.stringify({ scheme, palette, custom, showResources })).catch((err: unknown) => {
        console.error(`Could not save the appearance settings: ${err instanceof Error ? err.message : String(err)}`);
    });
}

useAppearance.subscribe((state, prev) => {
    if (state.scheme !== prev.scheme || state.palette !== prev.palette || state.custom !== prev.custom || state.showResources !== prev.showResources) persist(state);
});

AsyncStorage.getItem(KEY)
    .then((raw) => {
        if (raw) useAppearance.setState(parseStored(JSON.parse(raw)));
    })
    .catch((err: unknown) => {
        console.error(`Could not read the saved appearance settings; using the defaults: ${err instanceof Error ? err.message : String(err)}`);
    });
