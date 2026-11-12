// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A warning status the agent raises is a row of its own while the turn runs. */

import { render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { AgentActivity } from '../../components/AgentActivity';
import { useChatStore } from '../../stores/chatStore';
import type { StreamEvent } from '../../types';
import { applyStatusEvent, selectToolStatus } from '../statusEvents';

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

const downloading = 'Downloading the speech model, one time — 37% of 1.6 GB';

function runningTranscription(ids: { current: number }) {
    useChatStore.getState().addAgentStep({
        id: ++ids.current, type: 'tool', label: 'Using tool', tool: 'transcribe_media', active: true, timestamp: 1,
    });
}

it('shows the speech model download on the running call, as the sidecar relays it', () => {
    const ids = { current: 0 };
    runningTranscription(ids);
    // Relayed: a phase and a message, no `status` field.
    applyStatusEvent({ type: 'status', phase: 'recording', message: 'Audio ready' } as StreamEvent, ids);
    applyStatusEvent({ type: 'status', phase: 'speech_model_download', message: downloading } as StreamEvent, ids);

    const toolStatus = selectToolStatus(useChatStore.getState());
    expect(toolStatus).toBe(downloading);
    render(<AgentActivity steps={useChatStore.getState().agentSteps} isActive toolStatus={toolStatus} />);
    expect(screen.getByText(downloading)).toBeVisible();
    expect(screen.queryByText('Audio ready')).toBeNull();
});

it('keeps the finished download as a step of the turn', () => {
    const ids = { current: 0 };
    runningTranscription(ids);
    applyStatusEvent({ type: 'status', phase: 'speech_model_download', message: downloading } as StreamEvent, ids);
    applyStatusEvent({ type: 'status', phase: 'speech_model_downloaded', message: 'Downloaded the speech model (1.6 GB)' } as StreamEvent, ids);

    const steps = useChatStore.getState().agentSteps;
    expect(steps.map((s) => [s.type, s.label, s.active])).toEqual([
        ['tool', 'Using tool', true],
        ['status', 'Downloaded the speech model (1.6 GB)', false],
    ]);
    expect(useChatStore.getState().liveStatus).toBeNull();
});
