// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useIsFocused, useRouter } from 'expo-router';

import { PairingFlow } from '@/features/pairing';

export default function Pair() {
    const router = useRouter();
    const focused = useIsFocused();
    return <PairingFlow backActive={focused} initialStep="how" onPaired={() => router.replace('/')} />;
}
