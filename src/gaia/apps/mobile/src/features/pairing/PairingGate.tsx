// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { ReactNode } from 'react';
import { Platform } from 'react-native';

import { useConnectionStore } from '@/state/connection';
import { Screen, Spinner, Stack } from '@/ui';

import { PairingFlow } from './PairingFlow';

/**
 * The first-launch gate: with no PC connected the person sees the welcome and pairing screens, not the app.
 * A development build started with EXPO_PUBLIC_GAIA_URL already has a connection, so it skips this and
 * reaches GAIA over plain HTTP as before. At launch it waits for the saved paired PC to be read.
 */
export function PairingGate({ children }: { children: ReactNode }) {
    const connected = useConnectionStore((s) => s.connection !== null);
    const restored = useConnectionStore((s) => s.restored);
    // A browser can't pin a certificate: the web build uses the same-origin or seeded connection.
    if (Platform.OS === 'web') return <>{children}</>;
    // Paired, not reachable: an unreachable PC is the connection banner's to show, never a reason to re-pair.
    if (connected) return <>{children}</>;
    if (!restored) {
        return (
            <Screen banner={null}>
                <Stack flex={1} justify="center">
                    <Spinner />
                </Stack>
            </Screen>
        );
    }
    return <PairingFlow />;
}
