// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Redirect, useLocalSearchParams, useRouter } from 'expo-router';
import { useMemo } from 'react';

import { FilePreviewScreen } from '@/features/files';
import type { PreviewTarget } from '@/features/files/previewStore';

/** A PC file or library document on its own screen: `?chat=<id>` plus `path=<file>` or `doc=<id>&name=<file>`. */
export default function FilePreviewRoute() {
    const router = useRouter();
    const { chat, path, doc, name } = useLocalSearchParams<{ chat?: string; path?: string; doc?: string; name?: string }>();
    const target = useMemo<PreviewTarget | null>(() => {
        if (!chat) return null;
        if (path) return { kind: 'path', path, sessionId: chat };
        if (doc) return { kind: 'document', docId: doc, name: name ?? '', sessionId: chat };
        return null;
    }, [chat, path, doc, name]);
    if (!target) return <Redirect href="/documents" />;
    return <FilePreviewScreen target={target} onClose={() => (router.canGoBack() ? router.back() : router.replace('/documents'))} />;
}
