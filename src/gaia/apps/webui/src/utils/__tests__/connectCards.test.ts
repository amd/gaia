// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Two mailbox steps that fail for want of an account show one Connect card (#8347). */

import { beforeEach, describe, expect, it } from 'vitest';
import { addCard } from '../connectCards';
import { fromApiMessages } from '../chatStream';
import { useChatStore } from '../../stores/chatStore';
import type { RenderCardData } from '../../types';

const connect = (extra: Record<string, unknown> = {}): RenderCardData => ({
    render: 'connect_account',
    data: { kind: 'connect_account', providers: ['google', 'microsoft'], agent_id: 'installed:gaia', ...extra },
});
const table: RenderCardData = { render: 'table', data: { columns: ['A'], rows: [] } };

describe('addCard', () => {
    it('folds a second identical Connect card into the first', () => {
        expect(addCard([connect()], connect())).toEqual([connect()]);
    });

    it('keeps other cards and their order', () => {
        const cards = [table, connect()].reduce<RenderCardData[]>(addCard, []);
        expect(addCard(cards, connect())).toEqual([table, connect()]);
        expect(addCard(cards, table)).toEqual([table, connect(), table]);
    });

    it('lists every account the two cards offer', () => {
        const merged = addCard(
            [connect({ providers: ['google'] })],
            connect({ providers: ['microsoft'] }),
        );
        expect(merged).toHaveLength(1);
        expect((merged[0].data as { providers: string[] }).providers).toEqual(['google', 'microsoft']);
    });

    it('waits on a provider either card found not connected', () => {
        const merged = addCard(
            [connect({ reconnect: ['google', 'microsoft'] })],
            connect({ reconnect: ['microsoft'] }),
        );
        expect((merged[0].data as { reconnect?: string[] }).reconnect).toEqual(['microsoft']);
        const none = addCard([connect({ reconnect: ['google'] })], connect());
        expect((none[0].data as { reconnect?: string[] }).reconnect).toBeUndefined();
    });

    it('unions the access asked for, and asks for the declared set when a card names none', () => {
        const merged = addCard(
            [connect({ scopes: { google: ['mail.read'], microsoft: ['mail.read'] } })],
            connect({ scopes: { google: ['calendar.read'] } }),
        );
        expect((merged[0].data as { scopes?: Record<string, string[]> }).scopes).toEqual({
            google: ['mail.read', 'calendar.read'],
        });
    });

    it('keeps the account both steps used, and drops one they disagree on', () => {
        const same = addCard(
            [connect({ account: { google: 'work@example.com' } })],
            connect({ account: { google: 'work@example.com' } }),
        );
        expect((same[0].data as { account?: unknown }).account).toEqual({ google: 'work@example.com' });
        const split = addCard(
            [connect({ account: { google: 'work@example.com' } })],
            connect({ account: { google: 'home@example.com' } }),
        );
        expect((split[0].data as { account?: unknown }).account).toBeUndefined();
    });

    it('keeps cards for different agents apart', () => {
        expect(addCard([connect()], connect({ agent_id: 'installed:email' }))).toHaveLength(2);
    });
});

describe('one Connect card per reply', () => {
    beforeEach(() => useChatStore.setState({ cards: [] }));

    it('while streaming', () => {
        useChatStore.getState().appendCard(connect());
        useChatStore.getState().appendCard(connect());
        expect(useChatStore.getState().cards).toEqual([connect()]);
    });

    it('after a reload', () => {
        const step = (id: number) => ({
            id, type: 'tool', label: 'mail', active: false, timestamp: id,
            render: 'connect_account', data: connect().data,
        });
        const [message] = fromApiMessages([{
            id: 1, session_id: 's', role: 'assistant', content: 'Connect Google or Microsoft.',
            created_at: '2026-11-16T00:00:00Z', agent_steps: [step(1), step(2)],
        }]);
        expect(message.cards).toEqual([connect()]);
    });
});
