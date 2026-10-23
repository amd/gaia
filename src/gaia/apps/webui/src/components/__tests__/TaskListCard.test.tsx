// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TaskListCard, formatElapsed, pickTaskList } from '../TaskListCard';
import type { TaskListState } from '../../types';

const NOW = 1_800_000_000; // epoch seconds

function list(overrides: Partial<TaskListState> = {}): TaskListState {
    return {
        items: [
            { id: 1, content: 'Read the config', status: 'completed', started_at: NOW - 100, completed_at: NOW - 88 },
            { id: 2, content: 'Fix the bug', status: 'in_progress', started_at: NOW - 60, completed_at: null },
            { id: 3, content: 'Run the tests', status: 'pending', started_at: null, completed_at: null },
        ],
        source: 'agent',
        current_action: 'Running command: pytest',
        action_started_at: NOW - 818,
        updated_at: NOW,
        ...overrides,
    };
}

beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW * 1000);
});

afterEach(() => {
    vi.useRealTimers();
});

describe('formatElapsed', () => {
    it.each([
        [0, '0s'],
        [12.7, '12s'],
        [125, '2m 05s'],
        [818, '13m 38s'],
        [3840, '1h 04m'],
    ])('%s seconds reads %s', (seconds, text) => {
        expect(formatElapsed(seconds)).toBe(text);
    });
});

describe('pickTaskList', () => {
    it('prefers the newer of the saved and the streamed list', () => {
        const saved = list({ updated_at: NOW - 5 });
        const live = list({ updated_at: NOW });
        expect(pickTaskList(saved, live)).toBe(live);
        expect(pickTaskList(live, saved)?.updated_at).toBe(NOW);
    });

    it('is null for a chat with no list or an empty one', () => {
        expect(pickTaskList(undefined, undefined)).toBeNull();
        expect(pickTaskList(list({ items: [] }), null)).toBeNull();
    });
});

describe('TaskListCard', () => {
    it('shows every step with its state while the agent works', () => {
        render(<TaskListCard taskList={list()} running />);
        const rows = screen.getAllByRole('listitem');
        expect(rows.map((r) => r.textContent)).toEqual([
            'Read the config, done12s',
            'Fix the bug, in progress',
            'Run the tests, to do',
        ]);
        expect(screen.getByText('1 of 3')).toBeTruthy();
    });

    it('ticks the current action timer every second', () => {
        render(<TaskListCard taskList={list()} running />);
        expect(screen.getByTestId('task-list-action').textContent).toBe('Running command: pytest · 13m 38s');
        act(() => { vi.advanceTimersByTime(2000); });
        expect(screen.getByTestId('task-list-action').textContent).toBe('Running command: pytest · 13m 40s');
    });

    it('folds to one line, and back', () => {
        render(<TaskListCard taskList={list()} running />);
        fireEvent.click(screen.getByRole('button', { expanded: true }));
        expect(screen.queryAllByRole('listitem')).toHaveLength(0);
        expect(screen.getByRole('button').textContent).toContain('1 of 3· Running command: pytest · 13m 38s');
        fireEvent.click(screen.getByRole('button', { expanded: false }));
        expect(screen.getAllByRole('listitem')).toHaveLength(3);
    });

    it('starts folded when no turn is running, with no live action or spinner', () => {
        render(<TaskListCard taskList={list()} running={false} />);
        const head = screen.getByRole('button', { expanded: false });
        expect(head.textContent).toContain('· Fix the bug');
        expect(head.textContent).not.toContain('pytest');
        fireEvent.click(head);
        // A stopped turn's step is next, not spinning.
        expect(screen.getAllByRole('listitem')[1].textContent).toContain('to do');
        expect(screen.queryByTestId('task-list-action')).toBeNull();
    });

    it('says when everything is done', () => {
        const items = list().items.map((i) => ({ ...i, status: 'completed' as const, started_at: NOW - 10, completed_at: NOW }));
        render(<TaskListCard taskList={list({ items, current_action: null })} running={false} />);
        expect(screen.getByText('3 of 3 done')).toBeTruthy();
    });

    it('names a list derived from the plan field as a plan', () => {
        render(<TaskListCard taskList={list({ source: 'plan' })} running />);
        expect(screen.getByRole('region', { name: 'Plan' })).toBeTruthy();
    });
});
