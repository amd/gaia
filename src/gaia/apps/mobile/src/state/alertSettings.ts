// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';

import { ALERT_SETTINGS_KEY, DEFAULT_ALERT_SETTINGS, parseAlertSettings, type AlertEvent, type AlertMode, type AlertSettings } from '@webui/utils/alertSettings.core';

interface AlertSettingsState extends AlertSettings {
    /** True once the saved copy has been read; a change made earlier would be written over it. */
    hydrated: boolean;
    setMode(event: AlertEvent, mode: AlertMode): void;
    update(patch: Partial<Omit<AlertSettings, 'modes'>>): void;
}

/** The web's chat-alert settings, saved under the web's key. Only the modes and the sound apply on a phone. */
export const useAlertSettings = create<AlertSettingsState>((set, get) => ({
    ...DEFAULT_ALERT_SETTINGS,
    hydrated: false,
    setMode: (event, mode) => set({ modes: { ...get().modes, [event]: mode } }),
    update: (patch) => set(patch),
}));

const saved = (s: AlertSettingsState): AlertSettings => ({ modes: s.modes, sound: s.sound, flash: s.flash, keepAwake: s.keepAwake, batteryFloor: s.batteryFloor });

useAlertSettings.subscribe((state, prev) => {
    const next = JSON.stringify(saved(state));
    if (!state.hydrated || next === JSON.stringify(saved(prev))) return;
    AsyncStorage.setItem(ALERT_SETTINGS_KEY, next).catch((err: unknown) => {
        console.error(`Could not save the notification settings: ${err instanceof Error ? err.message : String(err)}`);
    });
});

export function hydrateAlertSettings(): Promise<void> {
    return AsyncStorage.getItem(ALERT_SETTINGS_KEY)
        .then((raw) => useAlertSettings.setState({ ...parseAlertSettings(raw), hydrated: true }))
        .catch((err: unknown) => {
            console.error(`Could not read the saved notification settings; using the defaults: ${err instanceof Error ? err.message : String(err)}`);
            useAlertSettings.setState({ hydrated: true });
        });
}

void hydrateAlertSettings();
