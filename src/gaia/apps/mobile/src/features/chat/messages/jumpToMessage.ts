// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

type Jump = (messageId: number) => boolean;

let current: Jump | null = null;

/** The mounted transcript takes jumps to its messages; returns the call that stops it. */
export function registerJumpToMessage(jump: Jump): () => void {
    current = jump;
    return () => {
        if (current === jump) current = null;
    };
}

/** Scrolls the open chat's transcript to a message. False when no chat is open or the message isn't loaded in it. */
export function jumpToMessage(messageId: number): boolean {
    return current ? current(messageId) : false;
}
