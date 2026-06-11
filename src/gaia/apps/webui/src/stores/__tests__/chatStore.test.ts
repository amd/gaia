// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '../chatStore';
import type { AgentStep } from '../../types';

const step = (id: number, label: string): AgentStep => ({
    id,
    type: 'thinking',
    label,
    active: true,
    timestamp: id,
});

describe('chatStore streaming state', () => {
    beforeEach(() => {
        // Reset the slice of state these tests touch.
        useChatStore.setState({
            isStreaming: false,
            streamingContent: '',
            agentSteps: [],
        });
    });

    it('resetStreaming clears the streaming flag, content, and steps in one call', () => {
        const s = useChatStore.getState();
        s.setStreaming(true);
        s.setStreamContent('partial answer from previous session');
        s.addAgentStep(step(1, 'analyzing'));

        // Sanity: state is populated as if a stream were in flight.
        expect(useChatStore.getState().isStreaming).toBe(true);
        expect(useChatStore.getState().streamingContent).not.toBe('');
        expect(useChatStore.getState().agentSteps).toHaveLength(1);

        // Switching sessions must wipe all of it so the new view starts clean
        // instead of mirroring the previous session's stream (issue #1580).
        useChatStore.getState().resetStreaming();

        const after = useChatStore.getState();
        expect(after.isStreaming).toBe(false);
        expect(after.streamingContent).toBe('');
        expect(after.agentSteps).toEqual([]);
    });

    it('resetStreaming is a no-op-safe call on already-clean state', () => {
        useChatStore.getState().resetStreaming();
        const after = useChatStore.getState();
        expect(after.isStreaming).toBe(false);
        expect(after.streamingContent).toBe('');
        expect(after.agentSteps).toEqual([]);
    });
});
