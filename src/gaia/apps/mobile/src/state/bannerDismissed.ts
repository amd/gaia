// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

import type { Banner } from '@/features/banners/pickBanner';

interface BannerDismissedState {
    /** The kind the person dismissed; it stays hidden until it stops being the banner, and lasts until the app restarts. */
    dismissed: Banner['id'] | null;
    dismiss(id: Banner['id']): void;
    /** Forgets a dismissal once its banner is gone, so the next occurrence shows again. */
    reconcile(banner: Banner | null): void;
}

export const useBannerDismissedStore = create<BannerDismissedState>((set, get) => ({
    dismissed: null,
    dismiss: (id) => set({ dismissed: id }),
    reconcile: (banner) => {
        const { dismissed } = get();
        if (dismissed && !(banner?.dismissible && banner.id === dismissed)) set({ dismissed: null });
    },
}));
