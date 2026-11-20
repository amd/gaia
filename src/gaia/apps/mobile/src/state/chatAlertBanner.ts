// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

import type { AlertEvent } from '@webui/utils/alertSettings.core';

import type { NoticeTone } from '@/ui';

export interface ChatAlertBanner {
    sessionId: string;
    event: AlertEvent;
    tone: NoticeTone;
    title: string;
    message: string;
}

interface ChatAlertBannerState {
    /** The one chat alert on show; a newer one replaces it. */
    alert: ChatAlertBanner | null;
    show(alert: ChatAlertBanner): void;
    dismiss(): void;
}

export const useChatAlertBannerStore = create<ChatAlertBannerState>((set) => ({
    alert: null,
    show: (alert) => set({ alert }),
    dismiss: () => set({ alert: null }),
}));
