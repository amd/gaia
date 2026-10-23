// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CONFIRM_DELETE_MS, ScheduleManager } from '../ScheduleManager';
import { useChatStore } from '../../stores/chatStore';
import { useScheduleStore } from '../../stores/scheduleStore';
import { parseScheduleInput } from '../../services/api';
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
    last_error: null,
    run_count: 0,
    error_count: 0,
    session_id: null,
    schedule_config: null,
};

const deleteSchedule = vi.fn<(name: string) => Promise<void>>(async () => {});

beforeEach(() => {
    deleteSchedule.mockClear();
    useChatStore.setState({ showSchedules: true });
    useScheduleStore.setState({
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
            "GAIA didn't understand that schedule. 25:00 isn't a time — use 0:00–23:59.",
        );
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
