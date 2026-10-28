// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScheduleManager } from '../ScheduleManager';
import { useChatStore } from '../../stores/chatStore';
import { useScheduleStore } from '../../stores/scheduleStore';
import * as api from '../../services/api';
import type { Schedule } from '../../types';

vi.mock('../../services/api');

const news: Schedule = {
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

beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.listSchedules).mockReset().mockResolvedValue({ schedules: [news], total: 1 });
    vi.mocked(api.updateSchedule).mockReset();
    useChatStore.setState({ showSchedules: true });
    useScheduleStore.setState({ schedules: [news], error: null, actionError: null, loading: false, errorCounts: null });
});

afterEach(() => vi.useRealTimers());

describe('failed pause', () => {
    it('keeps the list, shows the error, and keeps it across a poll until dismissed', async () => {
        const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
        vi.mocked(api.updateSchedule).mockRejectedValueOnce(new Error('API 500: {"detail":"db locked"}'));
        render(<ScheduleManager />);

        await user.click(await screen.findByRole('button', { name: /pause/i }));
        expect(await screen.findByText('db locked')).toBeInTheDocument();
        expect(screen.getByText('news')).toBeInTheDocument();

        await act(async () => { await vi.advanceTimersByTimeAsync(5500); });
        expect(vi.mocked(api.listSchedules).mock.calls.length).toBeGreaterThanOrEqual(2);
        expect(screen.getByText('db locked')).toBeInTheDocument();
        expect(screen.getByText('news')).toBeInTheDocument();

        await user.click(screen.getByRole('button', { name: 'Dismiss error' }));
        expect(screen.queryByText('db locked')).not.toBeInTheDocument();
    });
});

describe('reopening the manager', () => {
    it('does not show an error from the previous time it was open', async () => {
        const first = render(<ScheduleManager />);
        await act(async () => { useScheduleStore.setState({ actionError: 'db locked' }); });
        expect(await screen.findByText('db locked')).toBeInTheDocument();

        first.unmount();
        render(<ScheduleManager />);
        expect(await screen.findByText('news')).toBeInTheDocument();
        expect(screen.queryByText('db locked')).not.toBeInTheDocument();
    });
});
