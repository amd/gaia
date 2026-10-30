// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { useScheduleStore } from '../scheduleStore';
import type { Schedule, ScheduleResult } from '../../types';

vi.mock('../../services/api');

function schedule(over: Partial<Schedule> = {}): Schedule {
    return {
        id: 's1',
        name: 'news',
        interval_seconds: 3600,
        description: 'Every hour',
        prompt: 'summarise the news',
        status: 'active',
        created_at: null,
        last_run_at: null,
        next_run_at: null,
        last_result: null,
        run_count: 0,
        error_count: 0,
        session_id: null,
        schedule_config: null,
        last_error: null,
        ...over,
    };
}

type Results = { results: ScheduleResult[]; total: number };

function result(id: number): ScheduleResult {
    return { id, result: `r${id}` } as unknown as ScheduleResult;
}

const store = () => useScheduleStore.getState();
const flush = async () => {
    for (let i = 0; i < 4; i++) await Promise.resolve();
};

beforeEach(() => {
    vi.mocked(api.listSchedules).mockReset();
    vi.mocked(api.createSchedule).mockReset();
    vi.mocked(api.updateSchedule).mockReset();
    vi.mocked(api.deleteSchedule).mockReset();
    vi.mocked(api.getScheduleResults).mockReset();
    useScheduleStore.setState({
        schedules: [schedule()],
        errorCounts: null,
        error: null,
        actionError: null,
        loading: false,
        selectedSchedule: null,
        results: [],
        resultsLoading: false,
    });
});

describe('failed schedule actions', () => {
    it('keep the list and report a failed pause in actionError', async () => {
        vi.mocked(api.updateSchedule).mockRejectedValueOnce(new Error('API 500: {"detail":"db locked"}'));
        await store().updateScheduleStatus('news', 'paused');
        expect(store().actionError).toBe('db locked');
        expect(store().error).toBeNull();
        expect(store().schedules).toHaveLength(1);
    });

    it('keep the list and report a failed delete in actionError', async () => {
        vi.mocked(api.deleteSchedule).mockRejectedValueOnce(new Error('API 500: {"detail":"nope"}'));
        await store().deleteSchedule('news');
        expect(store().actionError).toBe('nope');
        expect(store().error).toBeNull();
        expect(store().schedules).toHaveLength(1);
    });

    it('keep the list on a failed create and leave the error to the form', async () => {
        vi.mocked(api.createSchedule).mockRejectedValueOnce(new Error('API 400: {"detail":"name taken"}'));
        await expect(store().createSchedule('news', 'every hour', 'p')).rejects.toThrow();
        expect(store().actionError).toBeNull();
        expect(store().error).toBeNull();
        expect(store().schedules).toHaveLength(1);
    });

    it('survive a poll that succeeds', async () => {
        vi.mocked(api.deleteSchedule).mockRejectedValueOnce(new Error('API 500: {"detail":"nope"}'));
        await store().deleteSchedule('news');
        vi.mocked(api.listSchedules).mockResolvedValueOnce({ schedules: [schedule()], total: 1 });
        await store().loadSchedules();
        expect(store().actionError).toBe('nope');
    });

    it('survive a poll that fails, without touching the load error', async () => {
        vi.mocked(api.updateSchedule).mockRejectedValueOnce(new Error('API 500: {"detail":"nope"}'));
        await store().updateScheduleStatus('news', 'paused');
        vi.mocked(api.listSchedules).mockRejectedValueOnce(new Error('offline'));
        await store().loadSchedules();
        expect(store().actionError).toBe('nope');
        expect(store().error).toBe('Failed to load schedules');
    });

    it('do not clear the load error when an action fails', async () => {
        useScheduleStore.setState({ error: 'Failed to load schedules' });
        vi.mocked(api.deleteSchedule).mockRejectedValueOnce(new Error('API 500: {"detail":"nope"}'));
        await store().deleteSchedule('news');
        expect(store().error).toBe('Failed to load schedules');
    });

    it('clear once the next action succeeds or the error is dismissed', async () => {
        useScheduleStore.setState({ actionError: 'old' });
        vi.mocked(api.updateSchedule).mockResolvedValueOnce(schedule({ status: 'paused' }));
        await store().updateScheduleStatus('news', 'paused');
        expect(store().actionError).toBeNull();

        useScheduleStore.setState({ actionError: 'old' });
        store().dismissActionError();
        expect(store().actionError).toBeNull();
    });

    it('show the spinner on first load even with a lingering action error', async () => {
        useScheduleStore.setState({ schedules: [], actionError: 'old' });
        let loadingDuringFetch: boolean | null = null;
        vi.mocked(api.listSchedules).mockImplementationOnce(async () => {
            loadingDuringFetch = store().loading;
            return { schedules: [], total: 0 };
        });
        await store().loadSchedules();
        expect(loadingDuringFetch).toBe(true);
    });
});

describe('loadResults', () => {
    it('drops a late response for a schedule that is no longer selected', async () => {
        let resolveA!: (v: Results) => void;
        vi.mocked(api.getScheduleResults).mockImplementation((name: string) =>
            name === 'a'
                ? new Promise<Results>((res) => { resolveA = res; })
                : Promise.resolve({ results: [result(2)], total: 1 }),
        );
        store().selectSchedule('a');
        store().selectSchedule('b');
        await flush();
        expect(store().results).toEqual([result(2)]);
        resolveA({ results: [result(1)], total: 1 });
        await flush();
        expect(store().results).toEqual([result(2)]);
        expect(store().resultsLoading).toBe(false);
    });

    it('drops a late response once the selection is cleared', async () => {
        let resolveA!: (v: Results) => void;
        vi.mocked(api.getScheduleResults).mockImplementation(
            () => new Promise<Results>((res) => { resolveA = res; }),
        );
        store().selectSchedule('a');
        store().selectSchedule(null);
        resolveA({ results: [result(1)], total: 1 });
        await flush();
        expect(store().results).toEqual([]);
        expect(store().resultsLoading).toBe(false);
    });
});
