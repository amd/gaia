// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Redirect, useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';

import { GaiaClientProvider } from '@/data';
import { devGalleryEnabled } from '@/features/dev/devGallery';
import { FileBrowserScreen } from '@/features/files';
import { FILE_BROWSER_START, fileBrowserScene, type FileBrowserScene } from '@/features/files/devScenes';
import { ScreenBannerProvider, UIProvider } from '@/ui';

const SCENES = Object.keys(FILE_BROWSER_START) as FileBrowserScene[];

/** The PC file browser on a fake PC; `?scene=root|folder|selected|search|searchStopped|denied`. */
export default function FilesGalleryRoute() {
    const { scene } = useLocalSearchParams<{ scene?: string }>();
    const router = useRouter();
    const picked = SCENES.find((s) => s === scene) ?? 'root';
    const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
    const [client] = useState(() => fileBrowserScene(picked));
    if (!devGalleryEnabled()) return <Redirect href="/" />;
    return (
        <QueryClientProvider client={queryClient}>
            <GaiaClientProvider client={client}>
                <UIProvider>
                    <ScreenBannerProvider banner={null}>
                        <FileBrowserScreen chatId="chat-1" onClose={() => router.replace('/')} onPreview={() => router.push('/dev/file-preview?scene=binary')} initial={FILE_BROWSER_START[picked]} />
                    </ScreenBannerProvider>
                </UIProvider>
            </GaiaClientProvider>
        </QueryClientProvider>
    );
}
