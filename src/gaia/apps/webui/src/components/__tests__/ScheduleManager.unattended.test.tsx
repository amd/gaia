// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A schedule runs unattended, so the form says what it can't do and a failure reads once.

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ScheduleManager } from '../ScheduleManager';
import { useChatStore } from '../../stores/chatStore';
import { useScheduleStore } from '../../stores/scheduleStore';
import type { Schedule } from '../../types';

vi.mock('../../services/api');

const failure = 'GAIA stopped this unattended run at "Writing file": that step needs your approval.';

const failed: Schedule = {
    id: 's1',
    name: 'Daily note',
    interval_seconds: 86400,
    description: 'Daily at 7:00 AM',
    description_parts: { kind: 'daily', count: null, unit: null, days: null, time: '07:00', start_hour: null, end_hour: null },
    prompt: 'Create a file called daily-note.txt',
    status: 'active',
    created_at: null,
    last_run_at: null,
    next_run_at: null,
    last_result: null,
    run_count: 1,
    error_count: 1,
    session_id: null,
    schedule_config: null,
    last_error: failure,
};

beforeEach(() => {
    useChatStore.setState({ showSchedules: true });
    useScheduleStore.setState({
        schedules: [failed],
        loading: false,
        error: null,
        selectedSchedule: null,
        results: [],
        resultsLoading: false,
        loadSchedules: vi.fn(async () => {}),
        loadResults: vi.fn(async () => {}),
    });
});

describe('schedules run unattended', () => {
    it('tells the user in the form that a schedule cannot write files or run commands', () => {
        render(<ScheduleManager />);
        fireEvent.click(screen.getByRole('button', { name: /New schedule/ }));
        expect(screen.getByText(/can't write files or run commands/)).toBeInTheDocument();
    });

    it('shows a failed run once when the history is open', () => {
        useScheduleStore.setState({
            selectedSchedule: 'Daily note',
            results: [{ id: 'r1', task_id: 's1', executed_at: '2026-11-12T15:00:00Z', result: null, error: failure }],
        });
        render(<ScheduleManager />);
        expect(screen.getAllByText(new RegExp('GAIA stopped this unattended run'))).toHaveLength(1);
    });

    it('still shows the failure on the card when the history is closed', () => {
        render(<ScheduleManager />);
        expect(screen.getAllByText(new RegExp('GAIA stopped this unattended run'))).toHaveLength(1);
    });
});
