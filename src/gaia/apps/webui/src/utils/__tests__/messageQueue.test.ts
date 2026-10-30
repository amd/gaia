// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import * as q from '../messageQueue';

const states = (items: q.QueuedMessage[]) => items.map((m) => [m.text, m.state]);

describe('message queue', () => {
    it('keeps messages in the order they were typed', () => {
        let items = q.enqueue([], 'first', 'queued');
        items = q.enqueue(items, 'second', 'queued');
        expect(q.nextToSend(items)?.text).toBe('first');
    });

    it('puts a message the reply refused back in line, saying why, until it is offered again', () => {
        const items = q.enqueue([], 'more', 'sending');
        const refused = q.steerRefused(items, items[0].id);
        expect(refused[0]).toMatchObject({ state: 'queued', note: q.STEER_REFUSED_NOTE });
        expect(q.nextToSend(refused)?.text).toBe('more');
        expect(q.promote(refused, items[0].id, 'queued', 'sending')[0].note).toBeUndefined();
    });

    it('sends nothing as a new turn while one is still being offered to the reply', () => {
        let items = q.enqueue([], 'offered', 'sending');
        items = q.enqueue(items, 'later', 'queued');
        expect(q.nextToSend(items)).toBeUndefined();
    });

    it('sends nothing new while a message is still going out, so none goes twice', () => {
        let items = q.enqueue([], 'going', 'starting');
        items = q.enqueue(items, 'later', 'queued');
        expect(q.nextToSend(items)).toBeUndefined();
    });

    it('holds back the rest behind a failed message, so order is kept', () => {
        let items = q.enqueue([], 'failed one', 'failed');
        items = q.enqueue(items, 'after it', 'queued');
        expect(q.nextToSend(items)).toBeUndefined();
        const [failed] = items;
        expect(q.nextToSend(q.promote(items, failed.id, 'failed', 'queued'))?.text).toBe('failed one');
    });

    it('drops a message once the running reply reads it, even before its POST answers', () => {
        const items = q.enqueue(q.enqueue([], 'same', 'sending'), 'same', 'delivered');
        expect(states(q.taken(items, 'same'))).toEqual([['same', 'delivered']]);
    });

    it('does not let a late POST success override what the stream already reported', () => {
        const [offered] = q.enqueue([], 'x', 'sending');
        const back = q.returned([offered], ['x']);
        expect(states(q.promote(back, offered.id, 'sending', 'delivered'))).toEqual([['x', 'queued']]);
    });

    it('never sends a message as a new turn while its POST to the reply is still open', () => {
        const items = q.enqueue([], 'in flight', 'sending');
        expect(q.needsOutcome(items)).toBe(false);
        expect(q.nextToSend(items)).toBeUndefined();
    });
});

describe('settling messages the chat did not see the reply take', () => {
    it('queues returned ones, drops read ones, and shows unknown ones as failed', () => {
        let items = q.enqueue([], 'returned', 'delivered');
        items = q.enqueue(items, 'read', 'delivered');
        items = q.enqueue(items, 'unknown', 'delivered');
        items = q.enqueue(items, 'waiting', 'queued');

        const settled = q.reconcile(items, { taken: ['read'], returned: ['returned'] });

        expect(states(settled)).toEqual([['returned', 'queued'], ['unknown', 'failed'], ['waiting', 'queued']]);
        expect(settled[1].error).toBe(q.UNCONFIRMED_ERROR);
    });

    it('matches repeated text one for one, so two identical messages are not both dropped', () => {
        let items = q.enqueue([], 'yes', 'delivered');
        items = q.enqueue(items, 'yes', 'delivered');

        expect(states(q.reconcile(items, { taken: ['yes'], returned: [] }))).toEqual([['yes', 'failed']]);
        expect(states(q.reconcile(items, { taken: ['yes'], returned: ['yes'] }))).toEqual([['yes', 'queued']]);
    });

    it('prefers sending again to losing a message when the same text was both taken and returned', () => {
        const items = q.enqueue([], 'again', 'delivered');
        expect(states(q.reconcile(items, { taken: ['again'], returned: ['again'] }))).toEqual([['again', 'queued']]);
    });

    it('leaves messages in other states alone', () => {
        let items = q.enqueue([], 'q', 'queued');
        items = q.enqueue(items, 'f', 'failed');
        items = q.enqueue(items, 's', 'sending');
        expect(q.reconcile(items, { taken: ['q', 'f', 's'], returned: [] })).toEqual(items);
    });

    it('marks delivered messages failed when the record cannot be read', () => {
        const items = q.enqueue(q.enqueue([], 'd', 'delivered'), 'q', 'queued');
        expect(states(q.failDelivered(items, 'no record'))).toEqual([['d', 'failed'], ['q', 'queued']]);
    });
});

describe('a message that did not go out', () => {
    it('goes back in its place when the chat was busy', () => {
        let items = q.enqueue([], 'first', 'starting');
        items = q.enqueue(items, 'second', 'queued');
        const back = q.notStarted(items, items[0].id);
        expect(states(back)).toEqual([['first', 'queued'], ['second', 'queued']]);
        expect(q.nextToSend(back)?.text).toBe('first');
    });

    it('is shown as failed with the reason', () => {
        const items = q.enqueue([], 'first', 'starting');
        const [failed] = q.notStarted(items, items[0].id, 'HTTP 500');
        expect([failed.state, failed.error]).toEqual(['failed', 'HTTP 500']);
    });

    it('is not brought back when its chat was deleted meanwhile', () => {
        expect(q.notStarted([], 42, 'gone')).toEqual([]);
    });
});

describe('restoring queues after a reload', () => {
    it('keeps each chat’s messages and their order', () => {
        const saved = {
            a: [{ id: 3, text: 'one', state: 'queued' }, { id: 4, text: 'two', state: 'queued' }],
            b: [{ id: 5, text: 'other', state: 'failed', error: 'x' }],
        };
        const restored = q.restore(JSON.parse(JSON.stringify(saved)));
        expect(states(restored.a)).toEqual([['one', 'queued'], ['two', 'queued']]);
        expect(restored.b[0].error).toBe('x');
    });

    it('asks about an offer that was in flight, and flags a send that was', () => {
        const restored = q.restore({
            a: [{ id: 1, text: 'offered', state: 'sending' }, { id: 2, text: 'going', state: 'starting' }],
        });
        expect(states(restored.a)).toEqual([['offered', 'delivered'], ['going', 'failed']]);
        expect(restored.a[1].error).toBe(q.INTERRUPTED_ERROR);
    });

    it('never reuses a saved id for a new message', () => {
        q.restore({ a: [{ id: 9_000, text: 'old', state: 'queued' }] });
        expect(q.item('new', 'queued').id).toBeGreaterThan(9_000);
    });

    it('skips anything malformed instead of guessing', () => {
        expect(q.restore(null)).toEqual({});
        expect(q.restore('nope')).toEqual({});
        expect(q.restore({ a: 'x', b: [{ id: 'x', text: 1 }, { id: 1, text: 't', state: 'bogus' }] })).toEqual({});
    });
});
