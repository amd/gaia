// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';

import { openChat } from '@/features/nav/navigation';
import { SchedulesScreen } from '@/features/schedules';

export default function Schedules() {
    const router = useRouter();
    return <SchedulesScreen onOpenChat={(sessionId) => openChat(router, sessionId)} />;
}
