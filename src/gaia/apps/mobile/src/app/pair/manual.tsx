// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useIsFocused, useRouter } from 'expo-router';

import { PairingFlow } from '@/features/pairing';

export default function PairManual() {
    const router = useRouter();
    const focused = useIsFocused();
    return <PairingFlow initialStep="manual" backActive={focused} onPaired={() => router.replace('/')} />;
}
