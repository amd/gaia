// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Redirect } from 'expo-router';

import { devGalleryEnabled } from '@/features/dev/devGallery';
import { UIProvider } from '@/ui';
import { UIGallery } from '@/ui/gallery/UIGallery';

export default function UIGalleryRoute() {
    if (!devGalleryEnabled()) return <Redirect href="/" />;
    return (
        <UIProvider>
            <UIGallery />
        </UIProvider>
    );
}
