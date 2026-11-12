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

async function load(...list: Schedule[]) {
    vi.mocked(api.listSchedules).mockResolvedValueOnce({ schedules: list, total: list.length });
    await useScheduleStore.getState().loadSchedules();
}

const done = () => useNotificationStore.getState().notifications.filter((n) => n.type === 'info');

beforeEach(() => {
    vi.mocked(api.listSchedules).mockReset();
    useScheduleStore.setState({ schedules: [], errorCounts: null, cleanRunCounts: {}, error: null, loading: false });
    useNotificationStore.setState({ notifications: [] });
});

describe('scheduled runs that finish', () => {
    it('raise an info notification that points at the chat', async () => {
        await load(schedule());
        await load(schedule({ run_count: 1, last_result: 'Three headlines today.' }));

        expect(done()).toHaveLength(1);
        const [n] = done();
        expect(n.title).toBe('Scheduled task "news" finished');
        expect(n.message).toBe('Three headlines today.');
        expect(n.sessionId).toBe('chat-1');
        expect(n.read).toBe(false);
    });

    it('wait for a run in flight to end', async () => {
        await load(schedule());
        await load(schedule({ run_count: 1, running: true }));
        expect(done()).toHaveLength(0);

        await load(schedule({ run_count: 1, last_result: 'done' }));
        expect(done()).toHaveLength(1);
    });

    it('notify once per run, not on every poll', async () => {
        await load(schedule());
        const finished = schedule({ run_count: 1, last_result: 'done' });
        await load(finished);
        await load(finished);

        expect(done()).toHaveLength(1);
    });

    it('stay silent for a run that failed', async () => {
        await load(schedule());
        await load(schedule({ run_count: 1, running: true }));
        await load(schedule({ run_count: 1, error_count: 1, last_error: 'boom', last_result: 'boom' }));

        expect(done()).toHaveLength(0);
    });

    it('are not news on the first load', async () => {
        await load(schedule({ run_count: 4, last_result: 'old answer' }));

        expect(done()).toHaveLength(0);
    });

    it('count a run that was in flight at the first load once it ends', async () => {
        await load(schedule({ run_count: 3, running: true }));
        await load(schedule({ run_count: 3, last_result: 'done' }));

        expect(done()).toHaveLength(1);
    });
});
