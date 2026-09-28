// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * "A blinking cursor means output is still streaming — it is a status
 * indicator, never decoration, and never runs when nothing is being written."
 * — docs/spec/gaia-design-language.mdx, Typography and spacing.
 *
 * The welcome screen had the rule exactly backwards: its cursor sat solid while
 * the headline typed itself out, then started blinking the moment the typing
 * finished — and stayed there, blinking at a finished sentence, for as long as
 * the screen was open. ChatView.cursor.test.tsx pins the same rule for a chat
 * transcript; this pins it for the headline, which is the first thing a new
 * user ever sees.
 */

import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WelcomeScreen } from '../WelcomeScreen';
import { useChatStore } from '../../stores/chatStore';

vi.mock('../../services/api');

function renderWelcome() {
    return render(
        <WelcomeScreen
            onNewTask={vi.fn()}
            onSendPrompt={vi.fn()}
            onStartAgentTask={vi.fn()}
        />,
    );
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

describe('the welcome cursor reports writing, and nothing else', () => {
    it('blinks while the headline is still typing itself out', () => {
        const { container } = renderWelcome();

        // Two characters in: the title is mid-write, so a cursor belongs there.
        act(() => { vi.advanceTimersByTime(130); });

        expect(
            container.querySelectorAll('.terminal-cursor').length,
            'text is appearing and nothing marks where',
        ).toBe(1);
    });

    it('leaves none once the headline has finished writing itself', () => {
        const { container } = renderWelcome();

        // The title, the pause and the subtitle each schedule their timer from
        // inside the effect the previous one woke, so a single long advance
        // only ever gets one link further along the chain. Each act() flushes
        // the effects that schedule the next link.
        for (let i = 0; i < 40; i++) act(() => { vi.advanceTimersByTime(1_000); });

        expect(
            container.querySelectorAll('.terminal-cursor').length,
            'nothing is being written — a cursor here is decoration blinking at a finished sentence',
        ).toBe(0);
    });
});
