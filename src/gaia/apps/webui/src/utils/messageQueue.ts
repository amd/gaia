// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Messages typed while GAIA is answering. Each waits here and goes out, in
 * order, as the next message once the reply ends; the user can instead offer
 * one to the running reply. A message is only ever removed once the chat holds
 * it, or by the user.
 */

/**
 * `sending`: offered to the running reply. `delivered`: it accepted, not yet read.
 * `queued`: sent after the reply. `starting`: going out as its own message now.
 * `failed`: could not be sent; waits for the user.
 */
export type QueuedState = 'sending' | 'delivered' | 'queued' | 'starting' | 'failed';

const STATES: readonly QueuedState[] = ['sending', 'delivered', 'queued', 'starting', 'failed'];

export interface QueuedMessage {
    id: number;
    text: string;
    state: QueuedState;
    /** Why a `failed` message did not go out. */
    error?: string;
    /** Why a `queued` message could not be added to the running reply. */
    note?: string;
}

/** What a finished turn did with the messages offered to it (GET /api/chat/followup). */
export interface FollowUpOutcome {
    taken: string[];
    returned: string[];
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
    return items.map((m) => (m.id === id && m.state === from ? { ...m, state: to, error: undefined, note: undefined } : m));
}

export const STEER_REFUSED_NOTE = "Can't add to this reply; it will send when the reply ends.";

/** The running reply refused a message offered to it: back in line, saying so. */
export function steerRefused(items: QueuedMessage[], id: number): QueuedMessage[] {
    return items.map((m) => (m.id === id && m.state === 'sending' ? { ...m, state: 'queued', note: STEER_REFUSED_NOTE } : m));
}

export function remove(items: QueuedMessage[], id: number): QueuedMessage[] {
    return items.filter((m) => m.id !== id);
}

/** A `starting` message did not go out: back in line, or shown as failed. Gone if its chat was deleted. */
export function notStarted(items: QueuedMessage[], id: number, error?: string): QueuedMessage[] {
    if (!items.some((m) => m.id === id && m.state === 'starting')) return items;
    return items.map((m) => {
        if (m.id !== id) return m;
        return error === undefined ? { ...m, state: 'queued', error: undefined } : { ...m, state: 'failed', error };
    });
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

/** The reply is over but the chat never heard what it did with these, so it must ask. */
export function needsOutcome(items: QueuedMessage[]): boolean {
    return items.some((m) => m.state === 'delivered');
}

export const UNCONFIRMED_ERROR = 'GAIA could not confirm it read this message.';

/**
 * Settle every `delivered` message against the server's record of the turn:
 * returned ones go out next, read ones are already in the chat, and one the
 * record does not mention is shown as not sent rather than dropped or doubled.
 */
export function reconcile(items: QueuedMessage[], outcome: FollowUpOutcome): QueuedMessage[] {
    const back = [...outcome.returned];
    const read = [...outcome.taken];
    const result: QueuedMessage[] = [];
    for (const m of items) {
        if (m.state !== 'delivered') {
            result.push(m);
            continue;
        }
        const r = back.indexOf(m.text);
        if (r !== -1) {
            back.splice(r, 1);
            result.push({ ...m, state: 'queued' });
            continue;
        }
        const t = read.indexOf(m.text);
        if (t !== -1) {
            read.splice(t, 1);
            continue;
        }
        result.push({ ...m, state: 'failed', error: UNCONFIRMED_ERROR });
    }
    return result;
}

/** Mark every `delivered` message as not sent, when the turn's record cannot be read. */
export function failDelivered(items: QueuedMessage[], error: string): QueuedMessage[] {
    return items.map((m) => (m.state === 'delivered' ? { ...m, state: 'failed', error } : m));
}

/**
 * The next message to send as its own turn. Nothing goes while one is still
 * offered to a reply, and a failed one holds back the rest so order is kept.
 */
export function nextToSend(items: QueuedMessage[]): QueuedMessage | undefined {
    if (items.some((m) => offered(m) || m.state === 'starting')) return undefined;
    const first = items.find((m) => m.state === 'queued' || m.state === 'failed');
    return first?.state === 'queued' ? first : undefined;
}

export const INTERRUPTED_ERROR = 'GAIA closed while this was being sent, so it may not have gone out. '
    + 'Check the chat above before sending it again.';

/**
 * Queues read back after a reload. A message whose offer to the reply was in
 * flight may or may not have arrived, so it is asked about like a delivered one;
 * one that was starting as its own message is shown as failed for the user to
 * judge. New ids continue after the saved ones.
 */
export function restore(saved: unknown): Record<string, QueuedMessage[]> {
    if (!saved || typeof saved !== 'object') return {};
    const out: Record<string, QueuedMessage[]> = {};
    for (const [sessionId, list] of Object.entries(saved as Record<string, unknown>)) {
        if (!Array.isArray(list)) continue;
        const items = list
            .filter((m): m is QueuedMessage =>
                !!m && typeof m === 'object'
                && typeof (m as QueuedMessage).id === 'number'
                && typeof (m as QueuedMessage).text === 'string'
                && STATES.includes((m as QueuedMessage).state))
            .map((m): QueuedMessage => {
                if (m.state === 'sending') return { ...m, state: 'delivered' };
                if (m.state === 'starting') return { ...m, state: 'failed', error: INTERRUPTED_ERROR };
                return m;
            });
        for (const m of items) nextId = Math.max(nextId, m.id + 1);
        if (items.length) out[sessionId] = items;
    }
    return out;
}
