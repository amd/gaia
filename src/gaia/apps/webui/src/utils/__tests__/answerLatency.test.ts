// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { answerLatencies } from '../answerLatency';
import type { Message } from '../../types';

const msg = (id: number, role: Message['role'], at: string, stats?: Message['stats']): Message => ({
    id, session_id: 's1', role, content: 'x', created_at: at, rag_sources: null, stats,
});

const stats = { tokens_per_second: 40, time_to_first_token: 1, input_tokens: 10, output_tokens: 20 };

describe('answerLatencies', () => {
    it('times an answer from its question', () => {
        const map = answerLatencies([msg(1, 'user', '2026-10-16T15:00:00Z'), msg(2, 'assistant', '2026-10-16T15:00:40Z', stats)]);
        expect(map.get(2)).toBe(40_000);
    });

    it('leaves out the time the turn waited on the user', () => {
        const map = answerLatencies([
            msg(1, 'user', '2026-10-16T15:00:00Z'),
            msg(2, 'assistant', '2026-10-16T15:01:45Z', { ...stats, waiting_seconds: 80 }),
        ]);
        expect(map.get(2)).toBe(25_000);
    });

    it('times only the first answer after a question', () => {
        const map = answerLatencies([
            msg(1, 'user', '2026-10-16T15:00:00Z'),
            msg(2, 'assistant', '2026-10-16T15:00:10Z'),
            msg(3, 'assistant', '2026-10-16T15:00:20Z'),
        ]);
        expect([...map.keys()]).toEqual([2]);
    });
});
