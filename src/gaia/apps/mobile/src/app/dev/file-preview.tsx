// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Redirect, useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';

import { GaiaClientProvider } from '@/data';
import { devGalleryEnabled } from '@/features/dev/devGallery';
import { FilePreviewScreen } from '@/features/files';
import { FILE_PREVIEW_PATH, filePreviewScene, type FilePreviewScene } from '@/features/files/devScenes';
import { ScreenBannerProvider, UIProvider } from '@/ui';

const SCENES = Object.keys(FILE_PREVIEW_PATH) as FilePreviewScene[];

/** The file preview screen on a fake PC; `?scene=loading|text|table|binary`. */
export default function FilePreviewGalleryRoute() {
    const { scene } = useLocalSearchParams<{ scene?: string }>();
    const router = useRouter();
    const picked = SCENES.find((s) => s === scene) ?? 'text';
    const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
    const [client] = useState(() => filePreviewScene(picked));
    const [target] = useState(() => ({ kind: 'path' as const, path: FILE_PREVIEW_PATH[picked], sessionId: 'chat-1' }));
    if (!devGalleryEnabled()) return <Redirect href="/" />;
    return (
        <QueryClientProvider client={queryClient}>
            <GaiaClientProvider client={client}>
                <UIProvider>
                    <ScreenBannerProvider banner={null}>
                        <FilePreviewScreen target={target} onClose={() => router.replace('/')} />
                    </ScreenBannerProvider>
                </UIProvider>
            </GaiaClientProvider>
        </QueryClientProvider>
    );
}
