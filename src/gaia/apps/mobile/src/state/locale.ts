// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';

import { isLocaleCode } from '@webui/i18n/registry';

const KEY = 'gaia.locale';

interface LocaleState {
    /** The language the person picked in the app; null follows the phone's. */
    locale: string | null;
    /** True once the saved pick has been read; the first frame waits for it so direction is right. */
    hydrated: boolean;
    setLocale(code: string | null): void;
}

export const useLocaleStore = create<LocaleState>((set) => ({
    locale: null,
    hydrated: false,
    setLocale: (locale) => set({ locale }),
}));

useLocaleStore.subscribe((state, prev) => {
    if (!state.hydrated || state.locale === prev.locale) return;
    const write = state.locale ? AsyncStorage.setItem(KEY, state.locale) : AsyncStorage.removeItem(KEY);
    write.catch((err: unknown) => {
        console.error(`Could not save the language: ${err instanceof Error ? err.message : String(err)}`);
    });
});

export function hydrateLocale(): Promise<void> {
    return AsyncStorage.getItem(KEY)
        .then((saved) => {
            useLocaleStore.setState({ locale: saved && isLocaleCode(saved) ? saved : null, hydrated: true });
        })
        .catch((err: unknown) => {
            console.error(`Could not read the saved language; following the phone: ${err instanceof Error ? err.message : String(err)}`);
            useLocaleStore.setState({ hydrated: true });
        });
}

void hydrateLocale();
