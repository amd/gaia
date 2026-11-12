// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A schedule can be changed in place, and its past runs are one labelled click away (#7428). */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ScheduleManager, scheduleInputText } from '../ScheduleManager';
import { useChatStore } from '../../stores/chatStore';
import { useScheduleStore } from '../../stores/scheduleStore';
import * as api from '../../services/api';
import type { Schedule } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const schedule: Schedule = {
    id: 'id-1',
    name: 'Morning brief',
    interval_seconds: 86400,
    prompt: 'Summarize my inbox',
    status: 'active',
    created_at: null,
    last_run_at: null,
    next_run_at: null,
    last_result: null,
    run_count: 0,
    error_count: 0,
    session_id: null,
    schedule_config: JSON.stringify({ interval_seconds: 86400, time_of_day: '08:00', raw_input: 'daily at 8am' }),
    description: 'Daily at 8:00 AM',
    description_parts: { kind: 'daily', count: null, unit: null, days: null, time: '08:00', start_hour: null, end_hour: null },
    last_error: null,
};

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ showSchedules: true });
    mockedApi.listSchedules.mockResolvedValue({ schedules: [schedule], total: 1 });
    mockedApi.getScheduleResults.mockResolvedValue({ results: [], total: 0 });
    mockedApi.parseScheduleInput.mockResolvedValue({
        interval_seconds: 86400, time_of_day: '09:00', start_hour: null, end_hour: null, days_of_week: [0, 1, 2, 3, 4],
        description: 'Weekdays at 9:00 AM', description_parts: null, next_run_at: null, valid: true,
    });
    useScheduleStore.setState({ schedules: [], loading: false, error: null, actionError: null, selectedSchedule: null });
});

describe('Editing a schedule', () => {
    it('opens the form with its own words, keeps the name, and saves through the edit route', async () => {
        mockedApi.editSchedule.mockResolvedValue({ ...schedule, prompt: 'Brief me on today', description: 'Weekdays at 9:00 AM' });
        render(<ScheduleManager />);

        fireEvent.click(await screen.findByRole('button', { name: /^Edit$/ }));

        expect(screen.getByRole('heading', { name: 'Editing “Morning brief”' })).toBeInTheDocument();
        expect(screen.getByLabelText('When to run')).toHaveValue('daily at 8am');
        expect(screen.getByLabelText('Name')).toHaveValue('Morning brief');
        expect(screen.getByLabelText('Name')).toHaveAttribute('readonly');
        expect(screen.getByLabelText('What GAIA does')).toHaveValue('Summarize my inbox');

        fireEvent.click(screen.getByRole('button', { name: 'Weekdays at 9' }));
        fireEvent.change(screen.getByLabelText('What GAIA does'), { target: { value: 'Brief me on today' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

        await waitFor(() => expect(mockedApi.editSchedule).toHaveBeenCalledWith('Morning brief', 'weekdays at 9am', 'Brief me on today'));
        expect(mockedApi.createSchedule).not.toHaveBeenCalled();
        await waitFor(() => expect(screen.queryByRole('heading', { name: /Editing/ })).toBeNull());
        expect(await screen.findByText('Brief me on today')).toBeInTheDocument();
    });

    it('keeps the form open with the reason when the edit is refused', async () => {
        mockedApi.editSchedule.mockRejectedValue(new Error("'Morning brief' is running now. Edit it once this run finishes."));
        render(<ScheduleManager />);

        fireEvent.click(await screen.findByRole('button', { name: /^Edit$/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

        expect(await screen.findByText(/is running now/)).toBeInTheDocument();
        expect(screen.getByRole('heading', { name: 'Editing “Morning brief”' })).toBeInTheDocument();
    });

    it('cancelling leaves the next New schedule empty', async () => {
        render(<ScheduleManager />);

        fireEvent.click(await screen.findByRole('button', { name: /^Edit$/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        fireEvent.click(screen.getByRole('button', { name: /New schedule/ }));

        expect(screen.getByLabelText('Name')).toHaveValue('');
        expect(screen.getByLabelText('Name')).not.toHaveAttribute('readonly');
        expect(screen.getByRole('button', { name: 'Create schedule' })).toBeInTheDocument();
    });
});

describe('Past runs', () => {
    it('has its own labelled toggle that says whether it is open', async () => {
        mockedApi.getScheduleResults.mockResolvedValue({
            results: [{ id: 'r1', task_id: 'id-1', executed_at: new Date().toISOString(), result: '3 new items', error: null }],
            total: 1,
        });
        render(<ScheduleManager />);

        const toggle = await screen.findByRole('button', { name: /Past runs/ });
        expect(toggle).toHaveAttribute('aria-expanded', 'false');
        fireEvent.click(toggle);

        expect(await screen.findByText('3 new items')).toBeInTheDocument();
        expect(toggle).toHaveAttribute('aria-expanded', 'true');
    });
});

describe('scheduleInputText', () => {
    it('falls back to the description for a row saved without its input', () => {
        expect(scheduleInputText({ ...schedule, schedule_config: null })).toBe('Daily at 8:00 AM');
    });
});
