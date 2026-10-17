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

    it('sends nothing as a new turn while one is still being offered to the reply', () => {
        let items = q.enqueue([], 'offered', 'sending');
        items = q.enqueue(items, 'later', 'queued');
        expect(q.nextToSend(items)).toBeUndefined();
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

    it('queues what the reply returned and anything it never confirmed', () => {
        let items = q.enqueue([], 'returned', 'delivered');
        items = q.enqueue(items, 'unconfirmed', 'delivered');
        items = q.returned(items, ['returned']);
        expect(states(q.settle(items))).toEqual([['returned', 'queued'], ['unconfirmed', 'queued']]);
    });

    it('never sends a message as a new turn while its POST to the reply is still open', () => {
        const items = q.settle(q.enqueue([], 'in flight', 'sending'));
        expect(states(items)).toEqual([['in flight', 'sending']]);
        expect(q.nextToSend(items)).toBeUndefined();
    });
});
