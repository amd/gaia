// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CONFIRM_DELETE_MS, ScheduleManager } from '../ScheduleManager';
import { useChatStore } from '../../stores/chatStore';
import { useScheduleStore } from '../../stores/scheduleStore';
import { ApiError, parseScheduleInput } from '../../services/api';
import type { Schedule } from '../../types';

vi.mock('../../services/api');

const schedule: Schedule = {
    id: 'id-1',
    name: 'morning-brief',
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
    schedule_config: null,
    description: 'Every day',
    description_parts: { kind: 'every', count: 1, unit: 'day', days: null, time: null, start_hour: null, end_hour: null },
    last_error: null,
};

const deleteSchedule = vi.fn<(name: string) => Promise<void>>(async () => {});

beforeEach(() => {
    deleteSchedule.mockClear();
    useChatStore.setState({ showSchedules: true });
    useScheduleStore.setState({
        draft: null,
        schedules: [],
        loading: false,
        error: null,
        loadSchedules: vi.fn(async () => {}),
        deleteSchedule,
    });
});

afterEach(() => {
    vi.useRealTimers();
});

describe('New schedule form', () => {
    it('labels every field', () => {
        render(<ScheduleManager />);
        fireEvent.click(screen.getByRole('button', { name: /New schedule/ }));
        expect(screen.getByLabelText('When to run').tagName).toBe('TEXTAREA');
        expect(screen.getByLabelText('Name').tagName).toBe('INPUT');
        expect(screen.getByLabelText('Prompt').tagName).toBe('TEXTAREA');
    });

    it('ties the reason a schedule was rejected to the schedule field', async () => {
        vi.mocked(parseScheduleInput).mockResolvedValue({
            interval_seconds: 0,
            time_of_day: null,
            start_hour: null,
            end_hour: null,
            days_of_week: null,
            description: "25:00 isn't a time — use 0:00–23:59.",
            next_run_at: null,
            valid: false,
        });
        render(<ScheduleManager />);
        fireEvent.click(screen.getByRole('button', { name: /New schedule/ }));
        const field = screen.getByLabelText('When to run');
        fireEvent.change(field, { target: { value: 'daily at 25:00' } });

        expect(await screen.findByText("25:00 isn't a time — use 0:00–23:59.")).toBeInTheDocument();
        expect(field).toHaveAttribute('aria-invalid', 'true');
        expect(field).toHaveAccessibleDescription(
            "That schedule can't be used. 25:00 isn't a time — use 0:00–23:59. "
            + 'Write it in English, like "every 30m", "daily at 9pm" or "every hour from 8am to 6pm on weekdays".',
        );
    });
});

describe('Reusing a schedule name', () => {
    it('marks the Name field with a plain message instead of the server line', async () => {
        const createSchedule = vi.fn(async () => {
            throw Object.assign(new ApiError('x', 409, "Task with name 'Morning brief' already exists", null), {
                httpStatus: 409,
            });
        });
        useScheduleStore.setState({ createSchedule });
        render(<ScheduleManager />);
        fireEvent.click(screen.getByRole('button', { name: /New schedule/ }));
        fireEvent.change(screen.getByLabelText('When to run'), { target: { value: 'every day' } });
        fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Morning brief' } });
        fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Hello' } });
        fireEvent.click(screen.getByRole('button', { name: 'Create schedule' }));

        const name = screen.getByLabelText('Name');
        expect(await screen.findByText('A schedule with this name already exists. Choose a different name.')).toBeInTheDocument();
        expect(screen.queryByText(/Task with name/)).not.toBeInTheDocument();
        expect(name).toHaveAttribute('aria-invalid', 'true');

        fireEvent.change(name, { target: { value: 'Morning brief 2' } });
        expect(name).toHaveAttribute('aria-invalid', 'false');
    });
});

describe('Schedule card', () => {
    it('says when a weekday schedule runs instead of its raw interval', () => {
        useScheduleStore.setState({
            schedules: [{
                ...schedule,
                description: 'Weekdays at 8:00 AM',
                description_parts: { kind: 'days', count: null, unit: null, days: [0, 1, 2, 3, 4], time: '08:00', start_hour: null, end_hour: null },
            }],
        });
        render(<ScheduleManager />);
        expect(screen.getByTitle(/^Weekdays at 8:00\sAM$/)).toHaveTextContent(/Weekdays at 8:00\sAM/);
        expect(screen.queryByText('1d')).not.toBeInTheDocument();
    });
});

describe('Schedule card delete', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        useScheduleStore.setState({ schedules: [schedule] });
    });

    it('still deletes when Confirm? is clicked late in the window', () => {
        render(<ScheduleManager />);
        fireEvent.click(screen.getByTitle('Delete'));
        act(() => { vi.advanceTimersByTime(CONFIRM_DELETE_MS - 1000); });
        expect(screen.getByTitle('Delete')).toHaveTextContent('Confirm?');
        fireEvent.click(screen.getByTitle('Delete'));
        expect(deleteSchedule).toHaveBeenCalledWith('morning-brief');
    });

    it('re-arming after a reset gets the full window, and unmount clears the timer', () => {
        expect(CONFIRM_DELETE_MS).toBeGreaterThanOrEqual(6000);
        const { unmount } = render(<ScheduleManager />);
        fireEvent.click(screen.getByTitle('Delete'));
        act(() => { vi.advanceTimersByTime(CONFIRM_DELETE_MS); });
        expect(screen.getByTitle('Delete')).toHaveTextContent('Delete');

        fireEvent.click(screen.getByTitle('Delete'));
        act(() => { vi.advanceTimersByTime(CONFIRM_DELETE_MS - 1000); });
        expect(screen.getByTitle('Delete')).toHaveTextContent('Confirm?');

        unmount();
        expect(vi.getTimerCount()).toBe(0);
        expect(deleteSchedule).not.toHaveBeenCalled();
    });
});
