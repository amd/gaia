// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';
import { useCallback } from 'react';

import { NewChatScreen } from '@/features/new-chat';

export default function NewChat() {
    const router = useRouter();
    const open = useCallback(
        (id: string) => router.replace({ pathname: '/chat/[id]', params: { id } }),
        [router],
    );
    return <NewChatScreen onOpen={open} />;
}
