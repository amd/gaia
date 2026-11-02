// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { keepFailedReply, persistedQuestion } from '../chatStream';
import type { Message } from '../../types';

const msg = (id: number, role: 'user' | 'assistant', content: string, extra: Partial<Message> = {}): Message => ({
    id, session_id: 's', role, content, created_at: '2026-06-10T00:00:00Z', rag_sources: null, ...extra,
});

describe('persistedQuestion', () => {
    it('finds a question that still has its temporary id by its place among the questions', () => {
        const server = [msg(1, 'user', 'a'), msg(2, 'assistant', 'A'), msg(3, 'user', 'b'), msg(4, 'assistant', 'oops')];
        const local = [server[0], server[1], msg(1_780_000_000_000, 'user', 'b'), msg(1_780_000_000_002, 'assistant', 'x', { failed: true })];
        expect(persistedQuestion(server, local, local[2])?.id).toBe(3);
    });

    it('uses the id when it is already the server one', () => {
        const server = [msg(1, 'user', 'a'), msg(2, 'assistant', 'A')];
        expect(persistedQuestion(server, server, server[0])?.id).toBe(1);
    });

    it('is null when the server never saved the question', () => {
        const server = [msg(1, 'user', 'a'), msg(2, 'assistant', 'A')];
        const local = [...server, msg(1_780_000_000_000, 'user', 'b')];
        expect(persistedQuestion(server, local, local[2])).toBeNull();
    });

    it('never picks an earlier identical question that was answered', () => {
        const server = [msg(1, 'user', 'same'), msg(2, 'assistant', 'A')];
        const local = [...server, msg(1_780_000_000_000, 'user', 'same')];
        expect(persistedQuestion(server, local, local[2])).toBeNull();
    });

    it('skips an earlier question the server never saved', () => {
        const server = [msg(1, 'user', 'q2'), msg(2, 'assistant', 'oops')];
        const local = [
            msg(1_780_000_000_000, 'user', 'q1'), msg(1_780_000_000_002, 'assistant', 'lost', { failed: true }),
            msg(1_780_000_000_100, 'user', 'q2'), msg(1_780_000_000_102, 'assistant', 'x', { failed: true }),
        ];
        expect(persistedQuestion(server, local, local[2])?.id).toBe(1);
    });

    it('pairs saved questions that still carry temporary ids in order', () => {
        const server = [msg(1, 'user', 'q1'), msg(2, 'assistant', 'e1'), msg(3, 'user', 'q2'), msg(4, 'assistant', 'e2')];
        const local = [
            msg(1_780_000_000_000, 'user', 'q1'), msg(1_780_000_000_002, 'assistant', 'e1'),
            msg(1_780_000_000_100, 'user', 'q2'), msg(1_780_000_000_102, 'assistant', 'e2'),
        ];
        expect(persistedQuestion(server, local, local[0])?.id).toBe(1);
        expect(persistedQuestion(server, local, local[2])?.id).toBe(3);
    });

    it('is null when the server holds a different question after the shared rows', () => {
        const server = [msg(1, 'user', 'a'), msg(2, 'user', 'other')];
        const local = [server[0], msg(1_780_000_000_000, 'user', 'b')];
        expect(persistedQuestion(server, local, local[1])).toBeNull();
    });

    it('throws for a question no longer in the view', () => {
        expect(() => persistedQuestion([], [], msg(9, 'user', 'gone'))).toThrow(/no longer in this chat/);
    });
});

describe('keepFailedReply', () => {
    const local = [msg(1_780_000_000_000, 'user', 'q'), msg(1_780_000_000_002, 'assistant', 'plain cause', { failed: true })];

    it("marks the server's reply for the same turn as failed", () => {
        const server = [msg(1, 'user', 'q'), msg(2, 'assistant', 'Sorry, something went wrong.')];
        const kept = keepFailedReply(local, server);
        expect(kept.map((m) => m.id)).toEqual([1, 2]);
        expect(kept[1].failed).toBe(true);
        expect(server[1].failed).toBeUndefined();
    });

    it('leaves the server rows alone when the turn did not fail', () => {
        const ok = [local[0], { ...local[1], failed: undefined }];
        const server = [msg(1, 'user', 'q'), msg(2, 'assistant', 'answer')];
        expect(keepFailedReply(ok, server)).toBe(server);
    });

    it('leaves them alone when the server moved on to another question', () => {
        const server = [msg(1, 'user', 'q'), msg(2, 'assistant', 'a'), msg(3, 'user', 'more'), msg(4, 'assistant', 'b')];
        expect(keepFailedReply(local, server)).toBe(server);
    });

    it('keeps Try again when an earlier unsaved turn makes the lists differ in length', () => {
        const withLost = [msg(1_779_000_000_000, 'user', 'lost'), msg(1_779_000_000_002, 'assistant', 'x', { failed: true }), ...local];
        const server = [msg(1, 'user', 'q'), msg(2, 'assistant', 'Sorry, something went wrong.')];
        expect(keepFailedReply(withLost, server)[1].failed).toBe(true);
    });

    it('never marks a question as a failed reply', () => {
        const server = [msg(1, 'assistant', 'hi'), msg(2, 'user', 'q')];
        expect(keepFailedReply(local, server)).toBe(server);
    });
});
