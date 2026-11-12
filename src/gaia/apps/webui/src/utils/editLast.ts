// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Message } from '../types';

interface KeyLike {
    key: string;
    shiftKey: boolean;
    altKey: boolean;
    ctrlKey: boolean;
    metaKey: boolean;
    nativeEvent?: { isComposing?: boolean };
}

/** Up-arrow in an empty message box opens the last sent message for editing. */
export function isEditLastKey(e: KeyLike, value: string): boolean {
    return e.key === 'ArrowUp'
        && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey
        && !e.nativeEvent?.isComposing
        && value === '';
}

/** The user's most recent message, the one Up-arrow edits. */
export function lastSentMessage(messages: readonly Message[]): Message | undefined {
    for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === 'user') return messages[i];
    }
    return undefined;
}
