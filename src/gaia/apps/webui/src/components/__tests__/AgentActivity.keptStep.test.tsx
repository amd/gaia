// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import { useChatStore } from '../../stores/chatStore';
import { applyStatusEvent } from '../../utils/statusEvents';
import type { AgentStep, StreamEvent } from '../../types';

vi.mock('../../services/api');

const DONE = 'Downloaded the speech model (2.4 GB)';

function transcribeCall(id: number): AgentStep {
    return {
        id, type: 'tool', label: 'Using tool', tool: 'transcribe_media', narration: 'Transcribing standup.wav',
        detail: 'path: standup.wav', active: false, success: true, result: 'done', timestamp: id,
    };
}

describe('a download the user waited on', () => {
    beforeEach(() => {
        useChatStore.setState({ agentSteps: [] });
    });

    it('stays in the steps of its turn once it ends, without a warning icon', () => {
        const ref = { current: 0 };
        applyStatusEvent({ type: 'status', phase: 'speech_model_download', message: 'Downloading the speech model, one time (2.4 GB)' } as StreamEvent, ref);
        applyStatusEvent({ type: 'status', phase: 'speech_model_downloaded', message: DONE } as StreamEvent, ref);
        const steps = [...useChatStore.getState().agentSteps, transcribeCall(ref.current + 1)];

        const { container } = render(<AgentActivity steps={steps} isActive={false} running={false} />);
        fireEvent.click(container.querySelector('.flow-steps-summary') as Element);

        expect(screen.getByText(DONE)).toBeInTheDocument();
        expect(container.querySelector('.flow-warning')).toBeNull();
    });

    it('is drawn the same from a saved turn', () => {
        const saved: AgentStep[] = [
            { id: 1, type: 'status', label: DONE, active: false, success: true, kept: true, timestamp: 1 },
            transcribeCall(2),
        ];

        const { container } = render(<AgentActivity steps={saved} isActive={false} running={false} />);
        fireEvent.click(container.querySelector('.flow-steps-summary') as Element);

        expect(screen.getByText(DONE)).toBeInTheDocument();
        expect(container.querySelector('.flow-warning')).toBeNull();
    });
});
