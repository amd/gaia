// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import * as messageQueue from '@webui/utils/messageQueue';

import type { ChatTurn } from '@/state/chatStreams';

export interface FirstSendDeps {
    createSession: () => Promise<{ id: string }>;
    // Resolves false when the chat exists but the send could not start.
    deliver: (sessionId: string, text: string) => Promise<boolean | void>;
    // Texts typed while the chat was being created; taking them empties that list.
    takeHeld: () => string[];
    // Moves the held texts into the chat's queue and sends them in order.
    flush: (sessionId: string) => Promise<void>;
    queueHeld: (sessionId: string, items: messageQueue.QueuedMessage[]) => void;
    describeError: (err: unknown) => string;
    firstNotSent: string;
}

export interface FirstSendState {
    text: string;
    // From the first Send until the chat's first turn is under way.
    sending: boolean;
    error: string | null;
    // The first message, shown at once while its chat is made.
    opening: string | null;
    // The chat to open once its first turn is under way.
    open: string | null;
    // The chat whose first turn is being waited on.
    waiting: string | null;
}

export interface FirstSend {
    getState: () => FirstSendState;
    subscribe: (listener: () => void) => () => void;
    setText: (text: string | ((current: string) => string)) => void;
    submit: (message: string, hold: (text: string) => void) => Promise<void>;
    // Feed the chat's turn once it exists: opens the chat, or puts the message back on failure.
    observe: (turn: ChatTurn | undefined) => void;
    setDeps: (deps: FirstSendDeps) => void;
}

// Creates the chat on the first send; messages typed meanwhile wait and follow into it.
export function createFirstSend(initialDeps: FirstSendDeps, initialText = ''): FirstSend {
    let state: FirstSendState = { text: initialText, sending: false, opening: null, error: null, open: null, waiting: null };
    let deps = initialDeps;
    let pending: { id: string; message: string } | null = null;
    const listeners = new Set<() => void>();

    const set = (patch: Partial<FirstSendState>) => {
        state = { ...state, ...patch };
        listeners.forEach((l) => l());
    };

    const putBack = (message: string, error: string) => {
        // Nothing was sent, so every message goes back in the box.
        set({ text: [message, ...deps.takeHeld()].join('\n\n'), error, sending: false, opening: null, waiting: null });
    };

    return {
        getState: () => state,
        subscribe: (listener) => {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
        setText: (text) => set({ text: typeof text === 'function' ? text(state.text) : text }),
        setDeps: (next) => {
            deps = next;
        },
        submit: async (message, hold) => {
            if (!message) return;
            if (state.sending) {
                hold(message);
                set({ text: '' });
                return;
            }
            set({ sending: true, opening: message, error: null, text: '' });
            try {
                const session = await deps.createSession();
                const sent = await deps.deliver(session.id, message);
                if (sent === false) {
                    const failed = deps
                        .takeHeld()
                        .map((text) => ({ ...messageQueue.item(text, 'failed'), error: deps.firstNotSent }));
                    deps.queueHeld(session.id, failed);
                    set({ sending: false, open: session.id });
                    return;
                }
                pending = { id: session.id, message };
                set({ waiting: session.id });
            } catch (err) {
                putBack(message, deps.describeError(err));
            }
        },
        observe: (turn) => {
            if (!pending || turn?.status === 'connecting') return;
            const { id, message } = pending;
            pending = null;
            if (turn?.error && turn.status === 'idle') {
                putBack(message, deps.describeError(turn.error));
                return;
            }
            void deps.flush(id);
            set({ sending: false, open: id, waiting: null });
        },
    };
}
