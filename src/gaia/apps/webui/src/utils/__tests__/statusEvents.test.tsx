// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A warning status the agent raises is a row of its own while the turn runs. */

import { render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { AgentActivity } from '../../components/AgentActivity';
import { useChatStore } from '../../stores/chatStore';
import type { StreamEvent } from '../../types';
import { applyStatusEvent } from '../statusEvents';

vi.mock('../../services/api');

const notice = 'MCP servers that could not start: weatherbox (not found).';

beforeEach(() => useChatStore.getState().resetStreaming());

it('shows a warning while the turn runs', () => {
    const ids = { current: 0 };
    applyStatusEvent({ type: 'status', status: 'warning', message: notice } as StreamEvent, ids);

    render(<AgentActivity steps={useChatStore.getState().agentSteps} isActive />);
    expect(screen.getByText(notice)).toBeVisible();
});

it('does not write a warning over the progress row before it', () => {
    const ids = { current: 0 };
    applyStatusEvent({ type: 'status', status: 'working', message: 'Waiting for another chat' } as StreamEvent, ids);
    applyStatusEvent({ type: 'status', status: 'warning', message: notice } as StreamEvent, ids);
    applyStatusEvent({ type: 'status', status: 'working', message: 'Reading your request' } as StreamEvent, ids);

    const warnings = useChatStore.getState().agentSteps.filter((s) => s.success === false);
    expect(warnings.map((s) => s.label)).toEqual([notice]);
    render(<AgentActivity steps={useChatStore.getState().agentSteps} isActive />);
    expect(screen.getByText(notice)).toBeVisible();
});

const queued = (queue: NonNullable<StreamEvent['queue']>): StreamEvent => ({
    type: 'status',
    status: 'working',
    phase: 'model_queue',
    message: "Waiting for the model — 'gaia' is using it for 'Gemma-4-E4B-it-GGUF' (8s)",
    queue,
});

it('says a wait for the model without the agent id or model file', () => {
    applyStatusEvent(queued({ event: 'queued', holder: 'gaia', waited_s: 8 }), { current: 0 });

    const line = useChatStore.getState().liveStatus ?? '';
    expect(line).toBe('Waiting for another chat to finish (8s)');
    expect(line).not.toMatch(/gaia|GGUF/);
});

it('says where the turn stands in line', () => {
    applyStatusEvent(queued({ event: 'queued', holder: 'gaia', position: 2, queue_length: 3, waited_s: 4 }), { current: 0 });

    expect(useChatStore.getState().liveStatus).toBe('Waiting for another chat to finish, #2 of 3 in line (4s)');
});
