// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

interface NextChatDocsState {
    /** Library documents held for the chat New chat is about to create (the web UI's `draftDocumentIds`). */
    ids: string[];
    add(ids: readonly string[]): void;
    remove(id: string): void;
    clear(): void;
}

export const useNextChatDocs = create<NextChatDocsState>((set) => ({
    ids: [],
    add: (ids) => set((s) => ({ ids: [...s.ids, ...ids.filter((id, i) => !s.ids.includes(id) && ids.indexOf(id) === i)] })),
    remove: (id) => set((s) => ({ ids: s.ids.filter((x) => x !== id) })),
    clear: () => set({ ids: [] }),
}));
