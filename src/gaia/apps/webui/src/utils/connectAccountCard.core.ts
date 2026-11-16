// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The `connect_account` card's payload and which connectors it waits on. Pure, so the phone app can import it. */

import type { Message } from '../types';

export const CONNECT_ACCOUNT_RENDER = 'connect_account';

export interface ConnectAccountPayload {
    kind: typeof CONNECT_ACCOUNT_RENDER;
    providers: string[];
    agent_id?: string;
    /** Providers connected already, whose fault is something signing in again fixes. */
    reconnect?: string[];
    /** Per provider, the access the failed call needs. */
    scopes?: Record<string, string[]>;
}

function isScopeMap(value: unknown, providers: unknown[]): boolean {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    return Object.entries(value as Record<string, unknown>).every(
        ([p, s]) =>
            providers.includes(p)
            && Array.isArray(s)
            && s.length > 0
            && s.every((x) => typeof x === 'string' && x.length > 0),
    );
}

/**
 * What a Connect press asks for: what the call needs, plus what the connection
 * already carries, so signing in again never takes access away. Empty when the
 * card names nothing, so the backend asks for what the agent declares.
 */
export function requestScopes(needed: string[] | undefined, held: string[] | undefined): string[] {
    if (!needed?.length) return [];
    return [...new Set([...(held ?? []), ...needed])];
}

export function isConnectAccountPayload(value: unknown): value is ConnectAccountPayload {
    if (!value || typeof value !== 'object') return false;
    const v = value as Record<string, unknown>;
    return (
        v.kind === CONNECT_ACCOUNT_RENDER &&
        Array.isArray(v.providers) &&
        v.providers.length > 0 &&
        v.providers.every((p) => typeof p === 'string' && p.length > 0) &&
        (v.agent_id === undefined || typeof v.agent_id === 'string') &&
        (v.reconnect === undefined
            || (Array.isArray(v.reconnect) && v.reconnect.every((p) => typeof p === 'string'))) &&
        (v.scopes === undefined || isScopeMap(v.scopes, v.providers))
    );
}

/** The providers whose becoming connected is the fix: those not connected when the tool ran. */
export function awaited(card: ConnectAccountPayload): string[] {
    return card.providers.filter((p) => !card.reconnect?.includes(p));
}

/**
 * The connectors a message's Connect card is waiting on; empty when it has no
 * card, or when every account on it was connected already and needs signing
 * in again, which the connector list cannot show.
 */
export function connectCardProviders(message: Pick<Message, 'cards'> | undefined): string[] {
    const found = new Set<string>();
    for (const card of message?.cards ?? []) {
        if (card.render === CONNECT_ACCOUNT_RENDER && isConnectAccountPayload(card.data)) {
            awaited(card.data).forEach((p) => found.add(p));
        }
    }
    return [...found];
}

/** Whether a message carries a Connect card at all. */
export function hasConnectCard(message: Pick<Message, 'cards'> | undefined): boolean {
    return (message?.cards ?? []).some(
        (card) => card.render === CONNECT_ACCOUNT_RENDER && isConnectAccountPayload(card.data),
    );
}
