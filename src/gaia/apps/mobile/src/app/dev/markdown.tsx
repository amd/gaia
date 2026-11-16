// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Redirect } from 'expo-router';

import { devGalleryEnabled } from '@/features/dev/devGallery';

import { MarkdownGallery } from '@/features/chat/markdown/MarkdownGallery';
import { UIProvider } from '@/ui';

export default function MarkdownGalleryRoute() {
    if (!devGalleryEnabled()) return <Redirect href="/" />;
    return (
        <UIProvider>
            <MarkdownGallery />
        </UIProvider>
    );
}
