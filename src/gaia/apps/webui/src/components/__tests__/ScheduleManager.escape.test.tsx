// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import * as api from '../../services/api';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ScheduleManager } from '../ScheduleManager';
import { useChatStore } from '../../stores/chatStore';
import { useScheduleStore } from '../../stores/scheduleStore';

vi.mock('../../services/api');

beforeEach(() => {
    useChatStore.setState({ showSchedules: true });
    useScheduleStore.setState({ draft: null, schedules: [], loading: false, error: null, loadSchedules: vi.fn(async () => {}) });
});

describe('ScheduleManager', () => {
    it('closes on Escape, like every other dialog', () => {
        render(<ScheduleManager />);
        expect(screen.getByRole('dialog', { name: /Schedule/ })).toBeInTheDocument();
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(useChatStore.getState().showSchedules).toBe(false);
    });

    it('takes focus when it opens and gives it back when it closes', () => {
        const trigger = document.createElement('button');
        document.body.appendChild(trigger);
        trigger.focus();
        const { unmount } = render(<ScheduleManager />);

        expect(screen.getByRole('dialog', { name: /Schedule/ }).contains(document.activeElement)).toBe(true);
        unmount();
        expect(trigger).toHaveFocus();
        trigger.remove();
    });

    it("an unparsed schedule shows the parser's reason", async () => {
        vi.mocked(api.parseScheduleInput).mockResolvedValue({
            valid: false,
            description: 'Try "every 30m" or "daily at 9pm".',
        } as never);
        render(<ScheduleManager />);
        fireEvent.click(screen.getByRole('button', { name: /New schedule/ }));
        fireEvent.change(screen.getByPlaceholderText(/Describe your schedule/), { target: { value: 'whenever the moon is blue' } });
        expect(await screen.findByText(/That schedule can.t be used/, {}, { timeout: 3000 })).toBeInTheDocument();
        expect(screen.getByText(/Try "every 30m"/).closest('.schedule-parsed-hint')).not.toBeNull();
    });

    it('keeps what was typed in the New schedule form when Escape closes the dialog', () => {
        const first = render(<ScheduleManager />);
        fireEvent.click(screen.getByRole('button', { name: /New schedule/ }));
        fireEvent.change(screen.getByLabelText('When to run'), { target: { value: 'every 3 hours' } });
        fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Stretch break' } });
        fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Stand up and stretch.' } });
        fireEvent.keyDown(document, { key: 'Escape' });
        first.unmount();

        render(<ScheduleManager />);
        expect(screen.getByLabelText('When to run')).toHaveValue('every 3 hours');
        expect(screen.getByLabelText('Name')).toHaveValue('Stretch break');
        expect(screen.getByLabelText('Prompt')).toHaveValue('Stand up and stretch.');
    });

    it('starts with the form closed again after Cancel', () => {
        const first = render(<ScheduleManager />);
        fireEvent.click(screen.getByRole('button', { name: /New schedule/ }));
        fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Stretch break' } });
        fireEvent.click(screen.getByRole('button', { name: /Cancel/ }));
        first.unmount();

        render(<ScheduleManager />);
        expect(screen.queryByLabelText('Name')).toBeNull();
    });

    it('drops the draft when a create finishes after the dialog was closed', async () => {
        let finish: () => void = () => {};
        const createSchedule = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
        useScheduleStore.setState({ createSchedule: createSchedule as never });
        const first = render(<ScheduleManager />);
        fireEvent.click(screen.getByRole('button', { name: /New schedule/ }));
        fireEvent.change(screen.getByLabelText('When to run'), { target: { value: 'every 3 hours' } });
        fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Stretch break' } });
        fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Stand up and stretch.' } });
        fireEvent.click(screen.getByRole('button', { name: /^Create/ }));
        expect(createSchedule).toHaveBeenCalled();
        first.unmount();
        expect(useScheduleStore.getState().draft).not.toBeNull();

        await act(async () => { finish(); });
        expect(useScheduleStore.getState().draft).toBeNull();
    });

    it('does not claim GAIA failed to understand a schedule it read but cannot run', async () => {
        vi.mocked(api.parseScheduleInput).mockResolvedValue({
            valid: false,
            description: "A schedule here repeats; it can't run just once.",
        } as never);
        render(<ScheduleManager />);
        fireEvent.click(screen.getByRole('button', { name: /New schedule/ }));
        fireEvent.change(screen.getByPlaceholderText(/Describe your schedule/), { target: { value: 'in 10 minutes' } });
        expect(await screen.findByText(/can't run just once/, {}, { timeout: 3000 })).toBeInTheDocument();
        expect(screen.queryByText(/didn.t understand/)).toBeNull();
    });
});
