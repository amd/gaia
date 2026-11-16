// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Redirect } from 'expo-router';

import { devGalleryEnabled } from '@/features/dev/devGallery';
import { UIProvider } from '@/ui';
import { FormsGallery } from '@/ui/gallery/FormsGallery';

export default function FormsGalleryRoute() {
    if (!devGalleryEnabled()) return <Redirect href="/" />;
    return (
        <UIProvider>
            <FormsGallery />
        </UIProvider>
    );
}
