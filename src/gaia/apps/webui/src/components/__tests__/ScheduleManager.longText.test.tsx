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
    description: 'Every hour',
    description_parts: { kind: 'every', count: 1, unit: 'hour', days: null, time: null, start_hour: null, end_hour: null },
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

const LONG_PROMPT = 'Search the web for news about our three main competitors and summarize anything that changed in their pricing, features or hiring, then compare it with last week\'s digest.';

describe('schedule card with long text', () => {
    it('keeps the full name and prompt on hover when the card cuts them short', () => {
        show({ ...base, name: 'weekly-research-digest-for-the-product-and-design-teams', prompt: LONG_PROMPT });
        expect(screen.getByText(LONG_PROMPT)).toHaveAttribute('title', LONG_PROMPT);
        expect(screen.getByText('weekly-research-digest-for-the-product-and-design-teams'))
            .toHaveAttribute('title', 'weekly-research-digest-for-the-product-and-design-teams');
    });
});
