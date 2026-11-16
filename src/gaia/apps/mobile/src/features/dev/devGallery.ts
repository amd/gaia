// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Platform } from 'react-native';

/** The `dev/*` gallery routes render in development builds, and in a web export made with EXPO_PUBLIC_DEV_GALLERY=1 (for design shots). */
export function devGalleryEnabled(): boolean {
    return __DEV__ || (Platform.OS === 'web' && process.env.EXPO_PUBLIC_DEV_GALLERY === '1');
}
