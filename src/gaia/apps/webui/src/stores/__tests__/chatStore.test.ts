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

describe('chatStore Hub navigation (#2206)', () => {
    beforeEach(() => {
        useChatStore.setState({ showHub: false, currentSessionId: null });
    });

    it('selecting a session leaves the Hub view (Home → session regression)', () => {
        // Home opens the full-screen Hub.
        useChatStore.getState().setShowHub(true);
        expect(useChatStore.getState().showHub).toBe(true);

        // Clicking a session must drop the Hub and switch to the chat view,
        // not strand the user on the Hub (#2206).
        useChatStore.getState().setCurrentSession('session-1');
        expect(useChatStore.getState().showHub).toBe(false);
        expect(useChatStore.getState().currentSessionId).toBe('session-1');
    });

    it('clearing the session (Home) does not fight setShowHub(true)', () => {
        // Home calls setCurrentSession(null) *then* setShowHub(true); the null
        // clear must not touch showHub or the Hub would never open.
        useChatStore.getState().setCurrentSession(null);
        useChatStore.getState().setShowHub(true);
        expect(useChatStore.getState().showHub).toBe(true);
        expect(useChatStore.getState().currentSessionId).toBeNull();
    });
});

describe('chatStore running-sessions registry (#1580)', () => {
    beforeEach(() => {
        useChatStore.setState({ runningSessionIds: [] });
    });

    it('setRunningSessions stores the polled active set', () => {
        useChatStore.getState().setRunningSessions(['a', 'b']);
        expect(useChatStore.getState().runningSessionIds).toEqual(['a', 'b']);
    });

    it('setRunningSessions keeps a stable reference when the set is unchanged', () => {
        useChatStore.getState().setRunningSessions(['a', 'b']);
        const first = useChatStore.getState().runningSessionIds;

        // Same membership (order-insensitive) must not produce a new array, so
        // the 2.6s poll doesn't re-render the sidebar every tick.
        useChatStore.getState().setRunningSessions(['b', 'a']);
        expect(useChatStore.getState().runningSessionIds).toBe(first);
    });

    it('setRunningSessions replaces the array when membership changes', () => {
        useChatStore.getState().setRunningSessions(['a']);
        const first = useChatStore.getState().runningSessionIds;

        useChatStore.getState().setRunningSessions(['a', 'c']);
        const second = useChatStore.getState().runningSessionIds;

        expect(second).not.toBe(first);
        expect(second).toEqual(['a', 'c']);
    });

    it('clears to empty when no runs are active', () => {
        useChatStore.getState().setRunningSessions(['a']);
        useChatStore.getState().setRunningSessions([]);
        expect(useChatStore.getState().runningSessionIds).toEqual([]);
    });
});
