// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Redirect, useLocalSearchParams, useRouter } from 'expo-router';
import { devGalleryEnabled } from '@/features/dev/devGallery';
import { useState } from 'react';

import { GaiaClientProvider } from '@/data';
import { MemoryScreen } from '@/features/memory';
import { MEMORY_TABS } from '@/features/memory/tabs';
import { ProfileDevContext } from '@/features/memory/profile/ProfileCard';
import { memoryScene, profileSceneStart, type MemoryScene } from '@/features/memory/devScenes';
import { ScreenBannerProvider, UIProvider } from '@/ui';

const SCENES: readonly MemoryScene[] = ['on', 'off', 'loading', 'failed', 'actionFails', 'statsEmpty', 'statsManyTools', 'aboutEmpty', 'aboutFailed', 'aboutMany', 'goalsEmpty', 'goalsFailed', 'goalsStoreError', 'knowledgeRich', 'knowledgeEmpty', 'knowledgeFailed', 'knowledgeMany', 'profileIdle', 'profileScanning', 'profileReview', 'profileSaved', 'profileInferring', 'profileInferenceReview', 'profileFailed', 'profileOff'];

/** The Memory screen on a fake PC; `?scene=on|off|loading|failed|actionFails|statsEmpty|statsManyTools|aboutEmpty|aboutFailed|aboutMany|goalsEmpty|goalsFailed|goalsStoreError|knowledgeRich|knowledgeEmpty|knowledgeFailed|knowledgeMany|profileIdle|profileScanning|profileReview|profileSaved|profileInferring|profileInferenceReview|profileFailed|profileOff`, and `&tab=about|goals|profile|advanced`. */
export default function MemoryGalleryRoute() {
    const { scene, tab } = useLocalSearchParams<{ scene?: string; tab?: string }>();
    const router = useRouter();
    const picked = SCENES.find((s) => s === scene) ?? 'on';
    const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
    const [client] = useState(() => memoryScene(picked));
    if (!devGalleryEnabled()) return <Redirect href="/" />;
    return (
        <QueryClientProvider client={queryClient}>
            <GaiaClientProvider client={client}>
                <UIProvider>
                    <ScreenBannerProvider banner={null}>
                        <ProfileDevContext.Provider value={profileSceneStart(picked)}>
                            <MemoryScreen onBack={() => router.replace('/')} initialTab={MEMORY_TABS.find((entry) => entry.key === tab)?.key} />
                        </ProfileDevContext.Provider>
                    </ScreenBannerProvider>
                </UIProvider>
            </GaiaClientProvider>
        </QueryClientProvider>
    );
}
