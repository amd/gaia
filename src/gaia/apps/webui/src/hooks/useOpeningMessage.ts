// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState, type MutableRefObject } from 'react';
import { useChatStore, type PendingPrompt } from '../stores/chatStore';
import type { Message } from '../types';

/** A new chat's first message, drawn before it is sent; the sent copy keeps its key, so nothing redraws. */
const OPENING_MESSAGE_ID = -2;
const OPENING_KEY = 'opening';

function withOpening(messages: Message[], text: string | null | undefined, sessionId: string): Message[] {
    if (!text || messages.some((m) => m.role === 'user' && m.content.trim() === text)) return messages;
    return [...messages, {
        id: OPENING_MESSAGE_ID, session_id: sessionId, role: 'user', content: text, created_at: '', rag_sources: null,
    }];
}

interface OpeningOptions {
    sessionId: string;
    pendingPrompt: PendingPrompt | null;
    messages: Message[];
    messageCount: number;
    isLoadingMessages: boolean;
    initializing: boolean;
    mountedRef: MutableRefObject<boolean>;
    send: MutableRefObject<(text: string, options: { opening: true }) => Promise<boolean>>;
}

/**
 * Sends a prompt queued before the view mounted (a new chat's first message, or
 * "Ask GAIA" from the file browser), and draws it from the first frame.
 */
export function useOpeningMessage({
    sessionId, pendingPrompt, messages, messageCount, isLoadingMessages, initializing, mountedRef, send,
}: OpeningOptions) {
    // Left pendingPrompt, not yet sent.
    const openingRef = useRef(false);
    const [openingText, setOpeningText] = useState<string | null>(null);
    useEffect(() => {
        const pending = pendingPrompt?.text;
        // Wait for the history load, or it would replace the message just sent;
        // and for GAIA to start, or the send would refuse a message already taken.
        if (!pending || pendingPrompt.sending || useChatStore.getState().isLoadingMessages || initializing) return;
        // Marked, not cleared, so New chat in the tick before the send can still take it back.
        const handed = { ...pendingPrompt, sending: true };
        useChatStore.getState().setPendingPrompt(handed);
        openingRef.current = true;
        setOpeningText(pending.trim());
        // A timer, not rAF: rAF never fires while the window is hidden, and
        // marking the prompt re-runs this effect, so no cleanup may cancel it.
        setTimeout(() => {
            openingRef.current = false;
            const store = useChatStore.getState();
            if (store.pendingPrompt !== handed) {
                if (mountedRef.current) setOpeningText(null);
                return;
            }
            if (!mountedRef.current) {
                // Left for another chat: it goes out when this one is opened again.
                store.setPendingPrompt({ sessionId: handed.sessionId, text: handed.text });
                return;
            }
            store.setPendingPrompt(null);
            void send.current(pending, { opening: true })
                .finally(() => { if (mountedRef.current) setOpeningText(null); });
        }, 0);
    }, [pendingPrompt, isLoadingMessages, initializing]);

    // A new chat's first message shows from the first frame, with no history to wait for.
    const openingShown = !initializing && messageCount === 0
        ? pendingPrompt?.text.trim() || openingText
        : null;
    const openedTextRef = useRef<string | null>(null);
    if (openingShown && !openedTextRef.current) openedTextRef.current = openingShown;
    const openedMessage = openedTextRef.current
        ? messages.find((m) => m.role === 'user' && m.content.trim() === openedTextRef.current)
        : undefined;

    return {
        openingRef,
        openingShown,
        rows: withOpening(messages, openingShown, sessionId),
        isUnsent: (msg: Message) => msg.id === OPENING_MESSAGE_ID,
        openingKey: (msg: Message) => (msg.id === OPENING_MESSAGE_ID || msg === openedMessage ? OPENING_KEY : undefined),
    };
}
