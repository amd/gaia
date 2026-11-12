// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { useNotificationStore } from '../notificationStore';
import { useScheduleStore } from '../scheduleStore';
import type { Schedule } from '../../types';

vi.mock('../../services/api');

function schedule(over: Partial<Schedule> = {}): Schedule {
    return {
        id: 's1',
        name: 'news',
        interval_seconds: 3600,
        description: 'Every hour',
        description_parts: { kind: 'every', count: 1, unit: 'hour', days: null, time: null, start_hour: null, end_hour: null },
        prompt: 'summarise the news',
        status: 'active',
        created_at: null,
        last_run_at: null,
        next_run_at: null,
        last_result: null,
        run_count: 0,
        error_count: 0,
        session_id: 'chat-1',
        schedule_config: null,
        last_error: null,
        ...over,
    };
}

function serve(...list: Schedule[]) {
    vi.mocked(api.listSchedules).mockResolvedValueOnce({ schedules: list, total: list.length });
}

const errors = () => useNotificationStore.getState().notifications.filter((n) => n.type === 'error');

const ANNOUNCED_KEY = 'gaia-schedule-announced-errors';

/** A new app launch: in-memory state is gone, localStorage survives. */
function relaunch() {
    useScheduleStore.setState({ schedules: [], errorCounts: null, error: null, loading: false });
    useNotificationStore.setState({ notifications: [] });
}

beforeEach(() => {
    vi.mocked(api.listSchedules).mockReset();
    localStorage.removeItem(ANNOUNCED_KEY);
    relaunch();
});

describe('scheduled run failures', () => {
    it('raise an error notification carrying the run error', async () => {
        serve(schedule());
        await useScheduleStore.getState().loadSchedules();
        serve(schedule({ run_count: 1, error_count: 1, last_error: 'Run `gaia init`.' }));
        await useScheduleStore.getState().loadSchedules();

        const [n] = errors();
        expect(errors()).toHaveLength(1);
        expect(n.title).toBe('Scheduled task "news" failed');
        expect(n.message).toBe('Run `gaia init`.');
        expect(n.sessionId).toBe('chat-1');
    });

    it('notify once per failed run, not on every poll', async () => {
        serve(schedule());
        await useScheduleStore.getState().loadSchedules();
        const failed = schedule({ error_count: 1, last_error: 'boom' });
        serve(failed);
        await useScheduleStore.getState().loadSchedules();
        serve(failed);
        await useScheduleStore.getState().loadSchedules();

        expect(errors()).toHaveLength(1);
    });

    it('from while the app was closed reach the bell on the first load', async () => {
        serve(schedule({ error_count: 1, last_error: 'failed overnight' }));
        await useScheduleStore.getState().loadSchedules();

        expect(errors()).toHaveLength(1);
        expect(errors()[0].message).toBe('failed overnight');
    });

    it('already announced in a past session are not announced again after relaunch', async () => {
        serve(schedule({ error_count: 3, last_error: 'old failure' }));
        await useScheduleStore.getState().loadSchedules();
        expect(errors()).toHaveLength(1);

        relaunch();
        serve(schedule({ error_count: 3, last_error: 'old failure' }));
        await useScheduleStore.getState().loadSchedules();

        expect(errors()).toHaveLength(0);
    });

    it('newer than the last session are announced after relaunch', async () => {
        serve(schedule({ error_count: 3, last_error: 'old failure' }));
        await useScheduleStore.getState().loadSchedules();

        relaunch();
        serve(schedule({ error_count: 4, last_error: 'new failure' }));
        await useScheduleStore.getState().loadSchedules();

        expect(errors()).toHaveLength(1);
        expect(errors()[0].message).toBe('new failure');
    });

    it('are announced when the saved record is corrupt', async () => {
        localStorage.setItem(ANNOUNCED_KEY, '{not json');
        serve(schedule({ error_count: 2, last_error: 'boom' }));
        await useScheduleStore.getState().loadSchedules();

        expect(errors()).toHaveLength(1);
    });
});
