// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useLocalSearchParams, useRouter } from 'expo-router';

import { DocumentsScreen } from '@/features/documents';

export default function Documents() {
    const router = useRouter();
    const { chat } = useLocalSearchParams<{ chat?: string }>();
    return (
        <DocumentsScreen
            chatId={chat}
            onBrowseFiles={(id) => router.push({ pathname: '/files', params: { chat: id } })}
            onPreview={(id, doc, name) => router.push({ pathname: '/file-preview', params: { chat: id, doc, name } })}
        />
    );
}
