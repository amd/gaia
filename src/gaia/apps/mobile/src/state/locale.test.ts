// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import AsyncStorage from '@react-native-async-storage/async-storage';

import { hydrateLocale, useLocaleStore } from './locale';

describe('locale persistence', () => {
    beforeEach(async () => {
        await AsyncStorage.clear();
        useLocaleStore.setState({ locale: null, hydrated: false });
        await hydrateLocale();
    });

    it('keeps an explicit pick across a reload and forgets it when set back to the phone', async () => {
        useLocaleStore.getState().setLocale('ar');
        await new Promise((r) => setTimeout(r, 0));
        useLocaleStore.setState({ locale: null, hydrated: false });
        await hydrateLocale();
        expect(useLocaleStore.getState()).toMatchObject({ locale: 'ar', hydrated: true });

        useLocaleStore.getState().setLocale(null);
        await new Promise((r) => setTimeout(r, 0));
        expect(await AsyncStorage.getItem('gaia.locale')).toBeNull();
    });
});
