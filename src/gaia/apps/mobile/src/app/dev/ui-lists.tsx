// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Redirect } from 'expo-router';

import { devGalleryEnabled } from '@/features/dev/devGallery';
import { UIProvider } from '@/ui';
import { ListsGallery } from '@/ui/gallery/ListsGallery';

export default function UiListsGalleryRoute() {
    if (!devGalleryEnabled()) return <Redirect href="/" />;
    return (
        <UIProvider>
            <ListsGallery />
        </UIProvider>
    );
}
