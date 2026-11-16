// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

type OpenChatSender = (text: string) => void;

let open: OpenChatSender | null = null;

/** The mounted ChatScreen takes sends for its chat; returns the call that stops it. */
export function registerOpenChat(sender: OpenChatSender): () => void {
    open = sender;
    return () => {
        if (open === sender) open = null;
    };
}

/**
 * Sends `text` into the chat on screen as if typed and sent (the web's `gaia:send-message` event); it queues
 * behind a running reply. False when no chat is open or the text is blank, so nothing was sent.
 */
export function sendToOpenChat(text: string): boolean {
    if (!open || !text.trim()) return false;
    open(text);
    return true;
}
