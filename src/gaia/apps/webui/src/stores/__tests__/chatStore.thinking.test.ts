// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A second thought streamed after the first one closed starts on its own
// paragraph, as the saved view shows it.

import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '../chatStore';
import type { AgentStep } from '../../types';

const thought = (detail: string, extra: Partial<AgentStep> = {}): AgentStep => ({
    id: 1, type: 'thinking', label: 'Thinking', detail, active: true, timestamp: 1, ...extra,
});

const last = () => {
    const steps = useChatStore.getState().agentSteps;
    return steps[steps.length - 1];
};

describe('appendThinkingContent', () => {
    beforeEach(() => useChatStore.setState({ agentSteps: [] }));

    it('keeps joining pieces of a thought that is still streaming', () => {
        useChatStore.setState({ agentSteps: [thought('Let me wri')] });
        useChatStore.getState().appendThinkingContent('te it well.');
        expect(last().detail).toBe('Let me write it well.');
    });

    it('starts a new paragraph after a thought the answer closed', () => {
        useChatStore.setState({ agentSteps: [thought('Let me write it well.', { active: false, latencyMs: 40 })] });
        useChatStore.getState().appendThinkingContent('General knowledge question.');
        expect(last().detail).toBe('Let me write it well.\n\nGeneral knowledge question.');
        expect(last().active).toBe(true);
        expect(last().latencyMs).toBeUndefined();
    });

    it('adds no break when the new thought brings its own spacing', () => {
        useChatStore.setState({ agentSteps: [thought('Done.', { active: false, latencyMs: 40 })] });
        useChatStore.getState().appendThinkingContent(' Next.');
        expect(last().detail).toBe('Done. Next.');
    });
});
