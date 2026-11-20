// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { RenderCardData } from '../types';

export const CONNECT_ACCOUNT_RENDER = 'connect_account';

export interface ConnectAccountPayload {
    kind: typeof CONNECT_ACCOUNT_RENDER;
    providers: string[];
    agent_id?: string;
    /** Providers connected already, whose fault is something signing in again fixes. */
    reconnect?: string[];
    /** Per provider, the access the failed call needs. */
    scopes?: Record<string, string[]>;
    /** Per provider, the account the failed call used: its stable id or its address. */
    account?: Record<string, string>;
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

function isAccountMap(value: unknown, providers: unknown[]): boolean {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    return Object.entries(value as Record<string, unknown>).every(
        ([p, a]) => providers.includes(p) && typeof a === 'string' && a.length > 0,
    );
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
        (v.scopes === undefined || isScopeMap(v.scopes, v.providers)) &&
        (v.account === undefined || isAccountMap(v.account, v.providers))
    );
}

/**
 * One Connect card for two failed steps that ask the same agent for an account.
 * A provider stays "reconnect" only when both cards say so, and a provider either
 * card names no access for asks for what the agent declares.
 */
function mergeConnect(a: ConnectAccountPayload, b: ConnectAccountPayload): ConnectAccountPayload {
    const providers = [...new Set([...a.providers, ...b.providers])];
    const isReconnect = (card: ConnectAccountPayload, p: string) =>
        !card.providers.includes(p) || (card.reconnect?.includes(p) ?? false);
    const reconnect = providers.filter((p) => isReconnect(a, p) && isReconnect(b, p));
    const scopes: Record<string, string[]> = {};
    for (const p of providers) {
        const named = [a, b].filter((c) => c.providers.includes(p)).map((c) => c.scopes?.[p]);
        if (named.every((s) => s !== undefined)) scopes[p] = [...new Set(named.flat() as string[])];
    }
    const account: Record<string, string> = {};
    for (const p of providers) {
        const named = new Set([a, b].map((c) => c.account?.[p]).filter((x): x is string => !!x));
        if (named.size === 1) account[p] = [...named][0];
    }
    const merged: ConnectAccountPayload = { ...a, providers };
    if (reconnect.length > 0) merged.reconnect = reconnect;
    else delete merged.reconnect;
    if (Object.keys(scopes).length > 0) merged.scopes = scopes;
    else delete merged.scopes;
    if (Object.keys(account).length > 0) merged.account = account;
    else delete merged.account;
    return merged;
}

/** *cards* with *card* added, folding a Connect card into the one already there. */
export function addCard(cards: RenderCardData[], card: RenderCardData): RenderCardData[] {
    if (card.render === CONNECT_ACCOUNT_RENDER && isConnectAccountPayload(card.data)) {
        const incoming = card.data;
        const at = cards.findIndex(
            (c) => c.render === CONNECT_ACCOUNT_RENDER
                && isConnectAccountPayload(c.data)
                && c.data.agent_id === incoming.agent_id,
        );
        if (at >= 0) {
            const next = [...cards];
            next[at] = { render: CONNECT_ACCOUNT_RENDER, data: mergeConnect(cards[at].data as ConnectAccountPayload, incoming) };
            return next;
        }
    }
    return [...cards, card];
}
