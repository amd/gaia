// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey } from '@webui/i18n/registry';
import { create } from 'zustand';

interface RouteNoticeState {
    /** Why the app landed on Home instead of where it was sent; shown there until dismissed. */
    notice: MessageKey | null;
    show(key: MessageKey): void;
    dismiss(): void;
}

export const useRouteNoticeStore = create<RouteNoticeState>((set) => ({
    notice: null,
    show: (notice) => set({ notice }),
    dismiss: () => set({ notice: null }),
}));
