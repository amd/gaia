// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ScheduleManager } from '../ScheduleManager';
import { useChatStore } from '../../stores/chatStore';
import { useScheduleStore } from '../../stores/scheduleStore';
import type { Schedule } from '../../types';

vi.mock('../../services/api');

const base: Schedule = {
    id: 's1',
    name: 'news',
    interval_seconds: 3600,
    prompt: 'summarise the news',
    status: 'active',
    created_at: null,
    last_run_at: null,
    next_run_at: null,
    last_result: null,
    run_count: 1,
    error_count: 1,
    session_id: null,
    schedule_config: null,
    last_error: null,
};

function show(schedule: Schedule) {
    useScheduleStore.setState({ schedules: [schedule], loading: false, error: null, loadSchedules: vi.fn(async () => {}) });
    render(<ScheduleManager />);
}

beforeEach(() => {
    useChatStore.setState({ showSchedules: true });
});

describe('schedule card', () => {
    it("shows why the last run failed", () => {
        show({ ...base, last_error: 'The GAIA agent is not installed. Run `gaia init`.' });
        expect(screen.getByText('Last run failed: The GAIA agent is not installed. Run `gaia init`.')).toBeInTheDocument();
    });

    it('shows nothing once a run succeeds', () => {
        show(base);
        expect(screen.queryByText(/Last run failed/)).not.toBeInTheDocument();
    });
});
