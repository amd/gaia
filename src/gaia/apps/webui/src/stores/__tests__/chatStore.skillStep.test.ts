// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A skill GAIA picks up mid-call shows before that call, and the running call
// keeps receiving its own result.

import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '../chatStore';
import { agentEventToStep } from '../../utils/streamSteps';
import type { AgentStep } from '../../types';

const call = (id: number, active: boolean): AgentStep => ({
    id, type: 'tool', label: 'Using tool', tool: 'analyze_transcript', active, timestamp: id,
});

const skillStep = () => agentEventToStep(
    { type: 'skill_use', skill: 'analyze-recording', how: 'auto', loaded: ['analyze-recording'] },
    { current: 10 },
);

describe('addSkillStep', () => {
    beforeEach(() => useChatStore.setState({ agentSteps: [], skillUses: 0 }));

    it('maps skill_use to a finished use_skill row naming the skill', () => {
        expect(skillStep()).toMatchObject({
            type: 'tool', tool: 'use_skill', detail: 'analyze-recording',
            narration: 'Using skill: analyze-recording', active: false, success: true,
        });
    });

    it('puts the row before the running call, which still gets its result', () => {
        useChatStore.setState({ agentSteps: [call(1, true)] });

        useChatStore.getState().addSkillStep(skillStep());
        useChatStore.getState().updateLastToolStep({ active: false, result: 'Report ready' });

        const steps = useChatStore.getState().agentSteps;
        expect(steps.map((s) => s.tool)).toEqual(['use_skill', 'analyze_transcript']);
        expect(steps[1]).toMatchObject({ active: false, result: 'Report ready' });
        expect(steps[0].result).toBeUndefined();
    });

    it('adds the row at the end when no call is running', () => {
        useChatStore.setState({ agentSteps: [call(1, false)] });

        useChatStore.getState().addSkillStep(skillStep());

        expect(useChatStore.getState().agentSteps.map((s) => s.tool)).toEqual(['analyze_transcript', 'use_skill']);
    });

    it('counts a skill the model loaded itself without adding a row', () => {
        useChatStore.getState().addSkillStep(null);

        expect(useChatStore.getState().agentSteps).toEqual([]);
        expect(useChatStore.getState().skillUses).toBe(1);
    });
});
