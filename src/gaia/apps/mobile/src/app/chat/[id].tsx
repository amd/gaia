// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect } from 'react';

import { useSession } from '@/data';
import { AppErrorBoundary } from '@/features/errors/AppErrorBoundary';
import { ChatScreen } from '@/features/chat/ChatScreen';
import { leaveMissingChat } from '@/features/nav/launch';

export default function ChatRoute() {
    const { id } = useLocalSearchParams<{ id: string }>();
    const router = useRouter();
    const { error } = useSession(id);
    useEffect(() => {
        if (error) leaveMissingChat(error, router);
    }, [error, router]);
    return (
        <AppErrorBoundary resetKey={id}>
            <ChatScreen sessionId={id} onManageDocuments={() => router.push({ pathname: '/documents', params: { chat: id } })} />
        </AppErrorBoundary>
    );
}
