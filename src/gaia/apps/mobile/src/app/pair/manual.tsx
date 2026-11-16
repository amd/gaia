// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';

import { PairingFlow } from '@/features/pairing';

export default function PairManual() {
    const router = useRouter();
    return <PairingFlow initialStep="manual" onPaired={() => router.replace('/')} />;
}
