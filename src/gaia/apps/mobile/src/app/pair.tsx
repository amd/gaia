// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useIsFocused, useLocalSearchParams, useRouter } from 'expo-router';

import { PairingFlow } from '@/features/pairing';

export default function Pair() {
    const router = useRouter();
    const { from } = useLocalSearchParams<{ from?: string }>();
    const focused = useIsFocused();
    return (
        <PairingFlow
            backActive={focused}
            initialStep="how"
            onPaired={() => router.replace('/')}
            onExit={from === 'pcs' ? () => router.replace('/pair/pcs') : undefined}
        />
    );
}
