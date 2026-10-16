// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Messages typed while GAIA is answering. Each is first offered to the running
 * reply; one the reply cannot take waits here and goes out, in order, as the
 * next message once the reply ends.
 */

/** `sending`: offered to the running reply. `delivered`: it accepted, not yet read. `queued`: sent after the reply. */
export type QueuedState = 'sending' | 'delivered' | 'queued';

export interface QueuedMessage {
    id: number;
    text: string;
    state: QueuedState;
}

let nextId = 1;

export function item(text: string, state: QueuedState): QueuedMessage {
    return { id: nextId++, text, state };
}

export function enqueue(items: QueuedMessage[], text: string, state: QueuedState): QueuedMessage[] {
    return [...items, item(text, state)];
}

/** Move one message from `from` to `to`; a message already settled elsewhere stays put. */
export function promote(items: QueuedMessage[], id: number, from: QueuedState, to: QueuedState): QueuedMessage[] {
    return items.map((m) => (m.id === id && m.state === from ? { ...m, state: to } : m));
}

export function remove(items: QueuedMessage[], id: number): QueuedMessage[] {
    return items.filter((m) => m.id !== id);
}

// The stream can report on a message before its POST answers, so `sending` counts as offered.
const offered = (m: QueuedMessage) => m.state === 'delivered' || m.state === 'sending';

/** The running reply read a message: drop the oldest offered one with that text. */
export function taken(items: QueuedMessage[], text: string): QueuedMessage[] {
    const index = items.findIndex((m) => offered(m) && m.text === text);
    return index === -1 ? items : [...items.slice(0, index), ...items.slice(index + 1)];
}

/** The reply ended without reading these; each waits for the next turn instead. */
export function returned(items: QueuedMessage[], texts: string[]): QueuedMessage[] {
    const left = [...texts];
    return items.map((m) => {
        const at = offered(m) ? left.indexOf(m.text) : -1;
        if (at === -1) return m;
        left.splice(at, 1);
        return { ...m, state: 'queued' };
    });
}

/**
 * The reply is over: whatever it accepted but never confirmed reading waits for
 * the next turn too, so nothing the user wrote goes unanswered. A message still
 * `sending` is left for its POST to settle, so it can never go out twice.
 */
export function settle(items: QueuedMessage[]): QueuedMessage[] {
    return items.map((m) => (m.state === 'delivered' ? { ...m, state: 'queued' } : m));
}

/** The next message to send as its own turn, once nothing is offered to a reply. */
export function nextToSend(items: QueuedMessage[]): QueuedMessage | undefined {
    if (items.some((m) => m.state === 'sending')) return undefined;
    return items.find((m) => m.state === 'queued');
}
