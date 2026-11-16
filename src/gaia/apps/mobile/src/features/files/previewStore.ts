// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

/** What the preview opens: a file on the PC by path, or a library document by id; `sessionId` is the chat that may read it. */
export type PreviewTarget =
    | { kind: 'path'; path: string; sessionId: string }
    | { kind: 'document'; docId: string; name: string; sessionId: string; page?: number };

interface PreviewState {
    target: PreviewTarget | null;
    show(target: PreviewTarget): void;
    close(): void;
}

export const usePreviewStore = create<PreviewState>((set) => ({
    target: null,
    show: (target) => set({ target }),
    close: () => set({ target: null }),
}));
