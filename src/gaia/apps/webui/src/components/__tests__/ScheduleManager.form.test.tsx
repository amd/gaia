// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScheduleManager } from '../ScheduleManager';
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
        useScheduleStore.setState({ schedules: [schedule], selectedSchedule: null });
    });

    const askToDelete = () => fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const ask = () => screen.getByRole('alertdialog', { name: 'Delete the schedule "morning-brief"?' });

    it('asks in a dialog that names the schedule, with Cancel focused, and deletes nothing yet', () => {
        render(<ScheduleManager />);
        askToDelete();
        expect(ask()).toHaveTextContent("It won't run again");
        expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
        // A double-click on Delete no longer deletes.
        expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument();
        expect(deleteSchedule).not.toHaveBeenCalled();
    });

    it('Cancel keeps the schedule and the panel open', () => {
        render(<ScheduleManager />);
        askToDelete();
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(screen.queryByRole('alertdialog')).toBeNull();
        expect(deleteSchedule).not.toHaveBeenCalled();
        expect(screen.getByText('morning-brief')).toBeInTheDocument();
        expect(useChatStore.getState().showSchedules).toBe(true);
        // The dialog's click doesn't also open the card's run history.
        expect(useScheduleStore.getState().selectedSchedule).toBeNull();
    });

    it('Escape cancels only the dialog, not the Scheduled tasks panel', () => {
        render(<ScheduleManager />);
        askToDelete();
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(screen.queryByRole('alertdialog')).toBeNull();
        expect(deleteSchedule).not.toHaveBeenCalled();
        expect(useChatStore.getState().showSchedules).toBe(true);
    });

    it('deletes the schedule when confirmed', () => {
        render(<ScheduleManager />);
        askToDelete();
        fireEvent.click(screen.getByRole('button', { name: 'Delete schedule' }));
        expect(deleteSchedule).toHaveBeenCalledWith('morning-brief');
        expect(screen.queryByRole('alertdialog')).toBeNull();
    });

    it('says its chat stays, and that a run in progress stops', () => {
        useScheduleStore.setState({ schedules: [{ ...schedule, session_id: 'chat-1', running: true }] });
        render(<ScheduleManager />);
        askToDelete();
        expect(ask()).toHaveTextContent('Its chat stays, with the answers from past runs.');
        expect(ask()).toHaveTextContent('The run in progress stops now.');
    });

    it('does not mention a chat for a schedule that never ran', () => {
        render(<ScheduleManager />);
        askToDelete();
        expect(ask()).not.toHaveTextContent('chat');
        expect(ask()).not.toHaveTextContent('in progress');
    });
});
