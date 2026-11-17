// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { RenderCardData } from '../types';
import {
    CONNECT_ACCOUNT_RENDER,
    isConnectAccountPayload,
    type ConnectAccountPayload,
} from './connectAccountCard.core';

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
    const merged: ConnectAccountPayload = { ...a, providers };
    if (reconnect.length > 0) merged.reconnect = reconnect;
    else delete merged.reconnect;
    if (Object.keys(scopes).length > 0) merged.scopes = scopes;
    else delete merged.scopes;
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
