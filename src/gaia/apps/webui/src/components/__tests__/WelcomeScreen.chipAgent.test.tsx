// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A suggestion chip has to start its task on the agent the picker is showing.
 *
 * Only agents that ship `conversation_starters` used to carry their id through
 * the click; for every other agent the chips fell back to the generic list and
 * routed to the flagship, so selecting an agent and clicking a chip quietly
 * opened a session on a different one.
 */

import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { WelcomeScreen } from '../WelcomeScreen';
import { useChatStore } from '../../stores/chatStore';
import type { AgentInfo } from '../../types';

vi.mock('../../services/api');

const agent = (id: string, starters?: string[]) =>
    ({ id, name: id, description: '', conversation_starters: starters } as unknown as AgentInfo);

/** The chips only mount once the headline has finished typing itself out. */
function renderSettled(handlers: {
    onSendPrompt: (prompt: string) => void;
    onStartAgentTask: (agentId: string, prompt?: string) => void;
}) {
    render(
        <WelcomeScreen onNewTask={vi.fn()} onCreateAgent={vi.fn()} {...handlers} />,
    );
    act(() => void vi.advanceTimersByTime(20000));
}

beforeEach(() => {
    vi.useFakeTimers();
    useChatStore.setState({
        agents: [], activeAgentId: 'gaia', sessions: [], currentSessionId: null,
        messages: [], documents: [], isStreaming: false, streamingContent: '', agentSteps: [],
        isLoadingMessages: false, pendingPrompt: null, systemStatus: null, runningSessionIds: [],
    });
});

afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
});

describe('a suggestion chip runs on the selected agent', () => {
    it('carries the agent id when it has starters of its own', () => {
        useChatStore.setState({ agents: [agent('email', ['Triage my inbox'])], activeAgentId: 'email' });
        const onStartAgentTask = vi.fn();
        const onSendPrompt = vi.fn();
        renderSettled({ onSendPrompt, onStartAgentTask });

        act(() => screen.getByRole('button', { name: 'Triage my inbox' }).click());

        expect(onStartAgentTask).toHaveBeenCalledWith('email', 'Triage my inbox');
        expect(onSendPrompt).not.toHaveBeenCalled();
    });

    it('carries it for the generic chips too, when the agent ships no starters', () => {
        useChatStore.setState({ agents: [agent('email')], activeAgentId: 'email' });
        const onStartAgentTask = vi.fn();
        const onSendPrompt = vi.fn();
        renderSettled({ onSendPrompt, onStartAgentTask });

        const chip = screen.getAllByRole('button').find((b) => b.className.includes('chip'));
        expect(chip, 'no suggestion chip rendered').toBeTruthy();
        act(() => chip!.click());

        expect(
            onStartAgentTask.mock.calls[0]?.[0],
            'the chip opened a session on a different agent than the one selected',
        ).toBe('email');
        expect(onSendPrompt).not.toHaveBeenCalled();
    });

    it('leaves the agent unpinned when the selected id matches nothing', () => {
        // The planner's own default is the flagship, and it is the one place
        // that should decide -- not a stale id the agent list no longer knows.
        useChatStore.setState({ agents: [agent('email')], activeAgentId: 'deleted-agent' });
        const onStartAgentTask = vi.fn();
        const onSendPrompt = vi.fn();
        renderSettled({ onSendPrompt, onStartAgentTask });

        const chip = screen.getAllByRole('button').find((b) => b.className.includes('chip'));
        act(() => chip!.click());

        expect(onSendPrompt).toHaveBeenCalledTimes(1);
        expect(onStartAgentTask).not.toHaveBeenCalled();
    });
});
