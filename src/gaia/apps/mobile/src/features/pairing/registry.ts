// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Platform } from 'react-native';

import { createFakePairingService } from './fakePairingService';
import type { PairingService } from './service';

let current: PairingService | null = __DEV__ || Platform.OS === 'web' ? createFakePairingService() : null;

/** Mobile installs the real service at start-up; tests install their own. */
export function setPairingService(service: PairingService | null): void {
    current = service;
}

export function getPairingService(): PairingService {
    if (!current) {
        throw new Error(
            'This build has no pairing service. Install one with setPairingService() from features/pairing/registry.ts at start-up; development builds use the fake.',
        );
    }
    return current;
}
