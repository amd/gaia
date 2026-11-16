// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Redirect, useLocalSearchParams, useRouter } from 'expo-router';

import { sendToOpenChat } from '@/features/chat/sendToOpenChat';
import { FileBrowserScreen } from '@/features/files';

export default function Files() {
    const router = useRouter();
    const { chat } = useLocalSearchParams<{ chat?: string }>();
    if (!chat) return <Redirect href="/documents" />;
    const ask = (prompt: string) => {
        if (!sendToOpenChat(prompt)) return false;
        router.navigate({ pathname: '/chat/[id]', params: { id: chat } });
        return true;
    };
    return <FileBrowserScreen chatId={chat} onPreview={(path) => router.push({ pathname: '/file-preview', params: { chat, path } })} onAsk={ask} onClose={() => (router.canGoBack() ? router.back() : router.replace({ pathname: '/documents', params: { chat } }))} />;
}
