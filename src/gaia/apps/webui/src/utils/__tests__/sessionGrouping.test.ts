// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { groupSessionsByRecency } from '../sessionGrouping';
import type { Session } from '../../types';

// Local time, so day boundaries match the function's calendar-day math in any TZ.
const NOW = new Date(2026, 8, 29, 12, 0, 0);

function at(id: string, date: Date | string): Session {
    return {
        id,
        title: id,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: typeof date === 'string' ? date : date.toISOString(),
        model: '',
        system_prompt: null,
        message_count: 1,
        document_ids: [],
    };
}

const daysAgo = (n: number, hour = 12) => new Date(2026, 8, 29 - n, hour, 0, 0);

describe('groupSessionsByRecency', () => {
    it('buckets by calendar day relative to now', () => {
        const groups = groupSessionsByRecency([
            at('today-early', new Date(2026, 8, 29, 0, 1)),
            at('yesterday-late', new Date(2026, 8, 28, 23, 59)),
            at('three-days', daysAgo(3)),
            at('twenty-days', daysAgo(20)),
            at('ninety-days', daysAgo(90)),
        ], NOW);

        expect(groups.map((g) => [g.label, g.sessions.map((s) => s.id)])).toEqual([
            ['Today', ['today-early']],
            ['Yesterday', ['yesterday-late']],
            ['Previous 7 days', ['three-days']],
            ['Previous 30 days', ['twenty-days']],
            ['Older', ['ninety-days']],
        ]);
    });

    it('puts the 7- and 30-day edges in the nearer bucket', () => {
        const groups = groupSessionsByRecency([
            at('seven', daysAgo(7, 0)),
            at('thirty', daysAgo(30, 0)),
        ], NOW);
        expect(groups.map((g) => g.label)).toEqual(['Previous 7 days', 'Previous 30 days']);
    });

    it('orders newest first within a bucket', () => {
        const [today] = groupSessionsByRecency([
            at('morning', new Date(2026, 8, 29, 8)),
            at('noon', new Date(2026, 8, 29, 11)),
            at('dawn', new Date(2026, 8, 29, 5)),
        ], NOW);
        expect(today.sessions.map((s) => s.id)).toEqual(['noon', 'morning', 'dawn']);
    });

    it('drops empty buckets and handles no chats', () => {
        expect(groupSessionsByRecency([], NOW)).toEqual([]);
        expect(groupSessionsByRecency([at('a', daysAgo(2))], NOW).map((g) => g.label)).toEqual(['Previous 7 days']);
    });

    it('files an unparseable timestamp under Older', () => {
        const groups = groupSessionsByRecency([at('bad', 'not a date')], NOW);
        expect(groups).toEqual([{ label: 'Older', sessions: [expect.objectContaining({ id: 'bad' })] }]);
    });

    it('does not mutate the input order', () => {
        const input = [at('old', daysAgo(40)), at('new', daysAgo(0))];
        groupSessionsByRecency(input, NOW);
        expect(input.map((s) => s.id)).toEqual(['old', 'new']);
    });
});
