// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import * as api from '../../services/api';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ScheduleManager } from '../ScheduleManager';
import { useChatStore } from '../../stores/chatStore';
import { useScheduleStore } from '../../stores/scheduleStore';

vi.mock('../../services/api');

beforeEach(() => {
    useChatStore.setState({ showSchedules: true });
    useScheduleStore.setState({ schedules: [], loading: false, error: null, loadSchedules: vi.fn(async () => {}) });
});

describe('ScheduleManager', () => {
    it('closes on Escape, like every other dialog', () => {
        render(<ScheduleManager />);
        expect(screen.getByRole('dialog', { name: /Schedule/ })).toBeInTheDocument();
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(useChatStore.getState().showSchedules).toBe(false);
    });

    it("an unparsed schedule shows the parser's reason", async () => {
        vi.mocked(api.parseScheduleInput).mockResolvedValue({
            valid: false,
            description: 'Try "every 30m" or "daily at 9pm".',
        } as never);
        render(<ScheduleManager />);
        fireEvent.click(screen.getByRole('button', { name: /New schedule/ }));
        fireEvent.change(screen.getByPlaceholderText(/Describe your schedule/), { target: { value: 'whenever the moon is blue' } });
        expect(await screen.findByText(/didn.t understand that schedule/, {}, { timeout: 3000 })).toBeInTheDocument();
        expect(screen.getByText(/every 30m/)).toHaveClass('schedule-parsed-hint');
    });
});
