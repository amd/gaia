// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import { useChatStore } from '../../stores/chatStore';
import { applyStatusEvent } from '../../utils/statusEvents';
import type { AgentStep, StreamEvent } from '../../types';

vi.mock('../../services/api');

const NOTICE = 'Moved 3 older tool results out of the prompt (~12.4K tokens); still readable';

const steps: AgentStep[] = [
    { id: 1, type: 'status', label: 'Reading the files', active: false, timestamp: 1 },
    { id: 2, type: 'status', label: NOTICE, active: false, notice: true, timestamp: 2 },
    { id: 3, type: 'status', label: 'Working out how to answer', active: true, timestamp: 3 },
];

beforeEach(() => {
    useChatStore.setState({ agentSteps: [] });
});

it('shows a context reduction notice while the turn runs', () => {
    render(<AgentActivity steps={steps} isActive running />);
    expect(screen.getByText(NOTICE).closest('.flow-notice')).not.toBeNull();
});

it('keeps the notice once the turn is over, while progress lines go', () => {
    render(<AgentActivity steps={steps} isActive={false} />);
    expect(screen.getByText(NOTICE).closest('.flow-notice')).not.toBeNull();
    expect(screen.queryByText('Reading the files')).toBeNull();
});

it('adds the stream event as its own notice row that later progress never folds into', () => {
    const stepIdRef = { current: 0 };
    const event = { type: 'status', status: 'context_reduced', message: NOTICE } as StreamEvent;
    applyStatusEvent(event, stepIdRef);
    applyStatusEvent({ type: 'status', status: 'working', message: 'Working out how to answer' } as StreamEvent, stepIdRef);

    const saved = useChatStore.getState().agentSteps;
    expect(saved[0]).toMatchObject({ label: NOTICE, notice: true, active: false });
    expect(saved).toHaveLength(2);
});
