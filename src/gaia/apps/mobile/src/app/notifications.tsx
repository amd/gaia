// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';

import { openChat } from '@/features/nav/navigation';
import { NotificationCenterScreen } from '@/features/notifications';

export default function Notifications() {
    const router = useRouter();
    return (
        <NotificationCenterScreen
            onBack={() => (router.canGoBack() ? router.back() : router.replace('/'))}
            onOpenChat={(id) => openChat(router, id)}
        />
    );
}
