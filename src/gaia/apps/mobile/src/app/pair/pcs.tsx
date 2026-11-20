// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';

import { PairedPcsScreen } from '@/features/pairing/PairedPcsScreen';

export default function PairedPcs() {
    const router = useRouter();
    return <PairedPcsScreen onAdd={() => router.push({ pathname: '/pair', params: { from: 'pcs' } })} />;
}
