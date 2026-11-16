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

const infos = () => useNotificationStore.getState().notifications.filter((n) => n.type === 'info');

beforeEach(() => {
    vi.mocked(api.listSchedules).mockReset();
    useScheduleStore.setState({ schedules: [], errorCounts: null, finishedCounts: {}, error: null, loading: false });
    useNotificationStore.setState({ notifications: [] });
});

async function load(...list: Schedule[]) {
    serve(...list);
    await useScheduleStore.getState().loadSchedules();
}

describe('scheduled runs that finish', () => {
    it('raise a notification carrying the answer and the chat', async () => {
        await load(schedule());
        await load(schedule({ run_count: 1, running: true }));
        expect(infos()).toHaveLength(0);
        await load(schedule({ run_count: 1, last_result: 'Three headlines today.' }));

        const [n] = infos();
        expect(infos()).toHaveLength(1);
        expect(n.title).toBe('Scheduled task "news" finished');
        expect(n.message).toBe('Three headlines today.');
        expect(n.sessionId).toBe('chat-1');
        expect(n.read).toBe(false);
    });

    it('show the answer as plain text, without markdown symbols', async () => {
        await load(schedule());
        await load(schedule({ run_count: 1, last_result: '**Three headlines** today, see [the full story](https://example.com/story).' }));

        expect(infos()[0].message).toBe('Three headlines today, see the full story.');
    });

    it('notify once per run, not on every poll', async () => {
        await load(schedule());
        const done = schedule({ run_count: 1, last_result: 'ok' });
        await load(done);
        await load(done);

        expect(infos()).toHaveLength(1);
    });

    it('are not news on the first load', async () => {
        await load(schedule({ run_count: 4, last_result: 'old answer' }));

        expect(infos()).toHaveLength(0);
    });

    it('stay quiet when the run failed, which raises its own error', async () => {
        await load(schedule());
        await load(schedule({ run_count: 1, error_count: 1, last_error: 'boom', last_result: 'boom' }));

        expect(infos()).toHaveLength(0);
        expect(useNotificationStore.getState().notifications).toHaveLength(1);
    });

    it('wait for a Run now that is marked running before it is counted', async () => {
        await load(schedule({ run_count: 1, last_result: 'first' }));
        await load(schedule({ run_count: 1, running: true }));
        await load(schedule({ run_count: 2, running: true }));
        expect(infos()).toHaveLength(0);
        await load(schedule({ run_count: 2, last_result: 'second' }));

        expect(infos().map((n) => n.message)).toEqual(['second']);
    });
});
