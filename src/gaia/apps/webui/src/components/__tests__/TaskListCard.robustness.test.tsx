// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The task-list card under hostile payloads, stale lists, and load. */

import { act, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MAX_CARD_ITEMS, STALE_AFTER_STEPS, TaskListCard, parseTaskListEvent, pickTaskList } from '../TaskListCard';
import { useChatStore } from '../../stores/chatStore';
import type { TaskListItem, TaskListState } from '../../types';

const NOW = Date.now() / 1000;

function item(id: number, status: TaskListItem['status'], content = `step ${id}`): TaskListItem {
    return { id, content, status, started_at: status === 'pending' ? null : NOW - 10, completed_at: status === 'completed' ? NOW : null };
}

function ok(raw: unknown): TaskListState {
    const parsed = parseTaskListEvent(raw);
    if ('error' in parsed) throw new Error(parsed.error);
    return parsed.state;
}

describe('parseTaskListEvent rejects what it cannot trust', () => {
    it.each([
        [null],
        ['a string'],
        [{ items: 'nope' }],
        [{ items: [42] }],
        [{ items: [{ content: 'a', status: 'running' }] }],
        [{ items: [{ content: { html: '<b>' }, status: 'pending' }] }],
        [{ items: [null] }],
    ])('%j', (raw) => {
        expect('error' in parseTaskListEvent(raw)).toBe(true);
    });

    it('coerces bad numbers and an unknown source instead of drawing NaN', () => {
        const state = ok({
            items: [{ content: 'a', status: 'pending', started_at: 'soon', completed_at: Infinity }],
            source: 'evil',
            current_action: 12,
            action_started_at: 'x',
            steps_since_update: -3,
        });
        expect(state.items[0].started_at).toBeNull();
        expect(state.items[0].completed_at).toBeNull();
        expect(state.source).toBe('agent');
        expect(state.current_action).toBeNull();
        expect(state.steps_since_update).toBe(0);
    });

    it('cuts an oversized list and says how much it hid', () => {
        const items = Array.from({ length: 5000 }, (_, i) => ({ content: 'x'.repeat(10_000), status: 'pending' }));
        const state = ok({ items });
        expect(state.items).toHaveLength(MAX_CARD_ITEMS);
        expect(state.hidden).toBe(5000 - MAX_CARD_ITEMS);
        expect(state.items[0].content.length).toBeLessThanOrEqual(200);
    });

    it('a broken saved copy is treated as no list', () => {
        expect(pickTaskList({ items: 'nope' } as unknown as TaskListState, null)).toBeNull();
    });
});

describe('TaskListCard never shows a false picture', () => {
    it('renders markup as text', () => {
        render(<TaskListCard taskList={ok({ items: [{ content: '<img src=x onerror="window.pwned=1">', status: 'in_progress' }] })} running />);
        expect(document.querySelector('img')).toBeNull();
        expect(screen.getByText('<img src=x onerror="window.pwned=1">')).toBeTruthy();
    });

    it('says "still working" rather than "done" while the turn runs on', () => {
        const state = ok({ items: [item(1, 'completed'), item(2, 'completed')], current_action: 'Writing file: notes.txt' });
        const { rerender } = render(<TaskListCard taskList={state} running />);
        expect(screen.getByText('2 of 2 · still working')).toBeTruthy();
        rerender(<TaskListCard taskList={state} running={false} />);
        expect(screen.getByText('2 of 2 done')).toBeTruthy();
        // An earlier turn's finished list has no live action: it stays "done".
        rerender(<TaskListCard taskList={{ ...state, current_action: null }} running />);
        expect(screen.getByText('2 of 2 done')).toBeTruthy();
    });

    it('flags a list the model stopped updating, only while it works', () => {
        const state = ok({ items: [item(1, 'in_progress'), item(2, 'pending')], steps_since_update: STALE_AFTER_STEPS + 2 });
        const { rerender } = render(<TaskListCard taskList={state} running />);
        expect(screen.getByTestId('task-list-stale').textContent).toContain(`${STALE_AFTER_STEPS + 2} steps`);
        rerender(<TaskListCard taskList={{ ...state, steps_since_update: STALE_AFTER_STEPS - 1 }} running />);
        expect(screen.queryByTestId('task-list-stale')).toBeNull();
        rerender(<TaskListCard taskList={state} running={false} />);
        expect(screen.queryByTestId('task-list-stale')).toBeNull();
    });

    it('never counts hidden steps as done', () => {
        const items = Array.from({ length: 60 }, (_, i) => ({ content: `s${i}`, status: i < 50 ? 'completed' : 'pending' }));
        render(<TaskListCard taskList={ok({ items })} running={false} />);
        expect(screen.getByText('50 of 60')).toBeTruthy();
    });
});

describe('TaskListCard under load', () => {
    it('draws 50 steps and survives 200 rapid updates quickly', () => {
        const base = Array.from({ length: MAX_CARD_ITEMS }, (_, i) => item(i + 1, 'pending'));
        const { rerender } = render(<TaskListCard taskList={{ items: base }} running />);
        const t0 = performance.now();
        for (let n = 0; n < 200; n++) {
            const items = base.map((it, i) => ({ ...it, status: (i < n % MAX_CARD_ITEMS ? 'completed' : i === n % MAX_CARD_ITEMS ? 'in_progress' : 'pending') as TaskListItem['status'] }));
            rerender(<TaskListCard taskList={{ items, updated_at: n }} running />);
        }
        const perUpdate = (performance.now() - t0) / 200;
        // jsdom is far slower than a browser; a regression to per-row work
        // that scales badly still blows well past this.
        expect(perUpdate).toBeLessThan(25);
        expect(screen.getAllByRole('listitem')).toHaveLength(MAX_CARD_ITEMS);
    });

    it('keeps each chat to its own list', () => {
        act(() => {
            for (let n = 0; n < 20; n++) {
                useChatStore.getState().setLiveTaskList(`chat-${n % 4}`, { items: [item(1, 'in_progress', `chat ${n % 4} at ${n}`)], updated_at: n });
            }
        });
        const lists = useChatStore.getState().liveTaskLists;
        for (let c = 0; c < 4; c++) expect(lists[`chat-${c}`].items[0].content).toMatch(new RegExp(`^chat ${c} at`));
        act(() => useChatStore.getState().clearTaskList('chat-1'));
        expect(useChatStore.getState().liveTaskLists['chat-1']).toBeUndefined();
        expect(useChatStore.getState().liveTaskLists['chat-2']).toBeDefined();
    });
});
