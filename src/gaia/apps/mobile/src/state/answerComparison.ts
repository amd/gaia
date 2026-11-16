// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

import type { RetryResult } from '@/api/client.types';

export type ComparisonState =
    | { status: 'running'; model: string }
    | { status: 'failed'; model: string; error: string }
    | { status: 'ready'; result: RetryResult };

export interface Comparison {
    messageId: number;
    state: ComparisonState;
    /** Which answer is being kept right now, while that request runs. */
    keeping: 'original' | 'retry' | null;
}

interface ComparisonStore {
    /** At most one open comparison per chat. */
    byChat: Record<string, Comparison | undefined>;
    start(chat: string, messageId: number, model: string): void;
    /** Results for a message that is no longer being compared are dropped. */
    settle(chat: string, messageId: number, state: ComparisonState): void;
    setKeeping(chat: string, keeping: Comparison['keeping']): void;
    close(chat: string): void;
}

export const useComparisonStore = create<ComparisonStore>((set) => ({
    byChat: {},
    start: (chat, messageId, model) =>
        set((s) => ({ byChat: { ...s.byChat, [chat]: { messageId, state: { status: 'running', model }, keeping: null } } })),
    settle: (chat, messageId, state) =>
        set((s) => {
            const open = s.byChat[chat];
            if (!open || open.messageId !== messageId) return s;
            return { byChat: { ...s.byChat, [chat]: { ...open, state, keeping: null } } };
        }),
    setKeeping: (chat, keeping) =>
        set((s) => {
            const open = s.byChat[chat];
            return open ? { byChat: { ...s.byChat, [chat]: { ...open, keeping } } } : s;
        }),
    close: (chat) => set((s) => ({ byChat: { ...s.byChat, [chat]: undefined } })),
}));

/** The model a comparison's second answer comes from. */
export function retryModel(state: ComparisonState): string {
    return state.status === 'ready' ? state.result.model : state.model;
}
