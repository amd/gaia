// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { create } from 'zustand';

import type { GaiaNotification } from '@webui/types/agent';

interface PermissionPromptsState {
    /** Answered approvals by chat id, oldest first; feeds `repeatedAllowCount`. */
    history: Readonly<Record<string, readonly GaiaNotification[]>>;
}

// App-lifetime memory only; an approval is not worth persisting.
export const usePermissionPromptStore = create<PermissionPromptsState>(() => ({ history: {} }));

const NONE: readonly GaiaNotification[] = [];

export function recordAnsweredPrompt(sessionId: string, prompt: GaiaNotification, response: 'allow' | 'deny'): void {
    usePermissionPromptStore.setState((state) => ({
        history: { ...state.history, [sessionId]: [...(state.history[sessionId] ?? NONE), { ...prompt, response }] },
    }));
}

export function useAnsweredPrompts(sessionId: string): readonly GaiaNotification[] {
    return usePermissionPromptStore((state) => state.history[sessionId] ?? NONE);
}

const deadlines = new Map<string, number>();

/** When a prompt with only a `timeout_seconds` lapses: fixed at first sight, so remounts don't restart it. */
export function deadlineFor(sessionId: string, promptId: string, timeoutSeconds: number): number {
    const key = `${sessionId}\u0000${promptId}`;
    let at = deadlines.get(key);
    if (at === undefined) {
        at = Date.now() + timeoutSeconds * 1000;
        deadlines.set(key, at);
    }
    return at;
}
