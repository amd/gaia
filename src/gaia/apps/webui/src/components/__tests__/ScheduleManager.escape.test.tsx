// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
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
});
