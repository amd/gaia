// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '../chatStore';
import type { AgentStep, RenderCardData } from '../../types';

const step = (id: number, label: string): AgentStep => ({
    id,
    type: 'thinking',
    label,
    active: true,
    timestamp: id,
});

const tableCard: RenderCardData = { render: 'table', data: { columns: ['A'], rows: [] } };
const listCard: RenderCardData = { render: 'list', data: { items: [] } };

describe('chatStore streaming cards (#2108)', () => {
    // NOTE: deliberately NOT resetting `cards` here. If the beforeEach preset
    // `cards: []` on every test, the "starts with an empty cards array" test
    // below would trivially pass off that preset rather than off the store's
    // real default — masking the exact `cards ?? []`-style softening this
    // suite exists to catch. Each test that needs a clean starting point
    // resets `cards` itself, explicitly, at the top of its own body.
    beforeEach(() => {
        useChatStore.setState({
            isStreaming: false,
            streamingContent: '',
            agentSteps: [],
        });
    });

    it('starts with an empty cards array', () => {
        // Must be the first test in this file (before any cards mutation) so
        // this reads the store's real default, not a beforeEach override.
        expect(useChatStore.getState().cards).toEqual([]);
    });

    it('appendCard appends cards in insertion order', () => {
        useChatStore.setState({ cards: [] });
        useChatStore.getState().appendCard(tableCard);
        useChatStore.getState().appendCard(listCard);

        const cards = useChatStore.getState().cards;
        expect(cards).toHaveLength(2);
        expect(cards[0]).toEqual(tableCard);
        expect(cards[1]).toEqual(listCard);
    });

    it('clearCards empties the cards array', () => {
        useChatStore.setState({ cards: [] });
        useChatStore.getState().appendCard(tableCard);
        expect(useChatStore.getState().cards).toHaveLength(1);

        useChatStore.getState().clearCards();
        expect(useChatStore.getState().cards).toEqual([]);
    });

    it('resetStreaming clears cards along with the existing streaming state (#1580 guard)', () => {
        useChatStore.setState({ cards: [] });
        const s = useChatStore.getState();
        s.setStreaming(true);
        s.setStreamContent('partial answer from previous session');
        s.addAgentStep(step(1, 'analyzing'));
        s.appendCard(tableCard);

        // Sanity: state is populated as if a stream were in flight, cards included.
        expect(useChatStore.getState().isStreaming).toBe(true);
        expect(useChatStore.getState().streamingContent).not.toBe('');
        expect(useChatStore.getState().agentSteps).toHaveLength(1);
        expect(useChatStore.getState().cards).toHaveLength(1);

        // Switching sessions must wipe all of it — including in-flight cards —
        // so the new view never mirrors the previous session's stream
        // (issue #1580's session-switch leak guard, extended to cards by #2108).
        useChatStore.getState().resetStreaming();

        const after = useChatStore.getState();
        expect(after.isStreaming).toBe(false);
        expect(after.streamingContent).toBe('');
        expect(after.agentSteps).toEqual([]);
        expect(after.cards).toEqual([]);
    });
});
