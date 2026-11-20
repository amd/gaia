// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Stack } from 'expo-router';

// Settings is one drawer route holding its own stack, so back and the swipe pop a section to the list.
export const unstable_settings = { anchor: 'index' };

export default function SettingsLayout() {
    return <Stack screenOptions={{ headerShown: false }} />;
}
