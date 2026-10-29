// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A new schedule can be tried at once instead of waiting for its first due time (#5718). */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ScheduleManager } from '../ScheduleManager';
import { useChatStore } from '../../stores/chatStore';
import { useScheduleStore } from '../../stores/scheduleStore';
import * as api from '../../services/api';
import type { Schedule } from '../../types';

vi.mock('../../services/api');

const base: Schedule = {
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
};

function show(schedule: Schedule) {
    useScheduleStore.setState({ schedules: [schedule], loading: false, error: null, loadSchedules: vi.fn(async () => {}) });
    render(<ScheduleManager />);
}

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ showSchedules: true });
});

describe('Run now', () => {
    it('runs the schedule and shows it running', async () => {
        vi.mocked(api.runScheduleNow).mockResolvedValue({ ...base, running: true });
        show(base);

        await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Run now/ })); });

        expect(api.runScheduleNow).toHaveBeenCalledWith('news');
        expect(screen.getByRole('button', { name: /Running…/ })).toBeDisabled();
    });

    it('says why when the run could not start', async () => {
        vi.mocked(api.runScheduleNow).mockRejectedValue(new Error("'news' is already running"));
        show(base);

        await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Run now/ })); });

        expect(useScheduleStore.getState().error).toContain('already running');
    });
});
