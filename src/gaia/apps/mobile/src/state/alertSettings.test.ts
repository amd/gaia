// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import AsyncStorage from '@react-native-async-storage/async-storage';

import { ALERT_SETTINGS_KEY, DEFAULT_ALERT_SETTINGS } from '@webui/utils/alertSettings.core';

import { hydrateAlertSettings, useAlertSettings } from './alertSettings';

describe('alert settings persistence', () => {
    beforeEach(async () => {
        await AsyncStorage.clear();
        useAlertSettings.setState({ ...DEFAULT_ALERT_SETTINGS, hydrated: false });
    });

    it('reads what the web saved, keeps what is changed under the web key, and ignores a bad mode', async () => {
        await AsyncStorage.setItem(ALERT_SETTINGS_KEY, JSON.stringify({ modes: { finished: 'off', question: 'loud' }, sound: true }));
        await hydrateAlertSettings();
        expect(useAlertSettings.getState()).toMatchObject({ hydrated: true, sound: true, modes: { needs_approval: 'banner', question: 'banner', finished: 'off' } });

        useAlertSettings.getState().setMode('needs_approval', 'badge');
        await new Promise((r) => setTimeout(r, 0));
        expect(JSON.parse((await AsyncStorage.getItem(ALERT_SETTINGS_KEY)) ?? '{}')).toMatchObject({ modes: { needs_approval: 'badge', finished: 'off' }, sound: true });
    });
});
