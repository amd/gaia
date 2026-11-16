// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

import { layoutDirection } from '@/platform/direction';
import type { Direction } from '@/platform/direction.types';

interface DirectionNoticeState {
    /** The direction waiting for a restart; null when the layout already matches. */
    pending: Direction | null;
    dismiss(): void;
}

export const useDirectionNotice = create<DirectionNoticeState>((set) => ({
    pending: null,
    dismiss: () => set({ pending: null }),
}));

/** Call after a language pick: raises the restart notice when the new language flips the layout. */
export function notifyDirectionChange(next: Direction): void {
    useDirectionNotice.setState({ pending: next === layoutDirection() ? null : next });
}
