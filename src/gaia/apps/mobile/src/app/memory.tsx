// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';

import { MemoryScreen } from '@/features/memory';
import { openChat } from '@/features/nav/navigation';

export default function Memory() {
    const router = useRouter();
    return <MemoryScreen onBack={() => (router.canGoBack() ? router.back() : router.replace('/'))} onOpenChat={(id) => openChat(router, id)} />;
}
