// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A model that sends nothing gets a "still waiting" hint, not just a growing seconds count. */

import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MessageBubble } from '../MessageBubble';
import { MODEL_WAIT_HINT_S, isModelWaitLabel } from '../../utils/modelWait';
import { tEn } from '../../i18n/useT';
import { useModelStore } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import type { ActiveModel } from '../../types';
import type { Message } from '../../types';

const MODEL_WAIT_HINT = tEn('live.modelWaitHint');
const LOCAL_MODEL_WAIT_HINT = tEn('live.localModelWaitHint');

const streaming: Message = {
    id: -1,
    session_id: 's',
    role: 'assistant',
    content: '',
    created_at: '2026-10-15T00:00:00Z',
    rag_sources: null,
};

function renderBubble(liveStatus: string | null, awaitingUser = false) {
    return render(
        <MessageBubble
            message={streaming}
            isStreaming
            agentSteps={[]}
            agentStepsActive
            liveStatus={liveStatus}
            awaitingUser={awaitingUser}
        />,
    );
}

beforeEach(() => {
    vi.useFakeTimers();
});

afterEach(() => {
    vi.useRealTimers();
    useModelStore.setState({ active: null });
});

describe('model wait hint', () => {
    it('is absent one second before the threshold and shown at it', () => {
        renderBubble('Reading your request');
        act(() => { vi.advanceTimersByTime((MODEL_WAIT_HINT_S - 1) * 1000); });
        expect(screen.queryByText(MODEL_WAIT_HINT)).not.toBeInTheDocument();
        act(() => { vi.advanceTimersByTime(1000); });
        expect(screen.getByText(MODEL_WAIT_HINT)).toBeInTheDocument();
    });

    it('on a model on this PC, says a long prompt is slow instead of promising a retry', () => {
        useModelStore.setState({ active: { inference_remote: false } as ActiveModel });
        renderBubble('Reading your request');
        act(() => { vi.advanceTimersByTime(MODEL_WAIT_HINT_S * 1000); });
        expect(screen.getByText(LOCAL_MODEL_WAIT_HINT)).toBeInTheDocument();
        expect(screen.queryByText(MODEL_WAIT_HINT)).not.toBeInTheDocument();
    });

    function rerenderWith(rerender: ReturnType<typeof render>['rerender'], liveStatus: string) {
        rerender(
            <MessageBubble message={streaming} isStreaming agentSteps={[]} agentStepsActive
                liveStatus={liveStatus} />,
        );
    }

    it('restarts its clock once a tool has run in between', () => {
        const { rerender } = renderBubble('Reading your request');
        act(() => { vi.advanceTimersByTime((MODEL_WAIT_HINT_S - 5) * 1000); });
        rerenderWith(rerender, 'Running run_python');
        act(() => { vi.advanceTimersByTime(1000); });
        rerenderWith(rerender, 'Reading the tool results');
        act(() => { vi.advanceTimersByTime(10_000); });
        expect(screen.queryByText(MODEL_WAIT_HINT)).not.toBeInTheDocument();
    });

    it('still shows when the model goes silent after a long tool run', () => {
        const { rerender } = renderBubble('Running run_python');
        act(() => { vi.advanceTimersByTime(10 * MODEL_WAIT_HINT_S * 1000); });
        rerenderWith(rerender, 'Reading the tool results');
        act(() => { vi.advanceTimersByTime((MODEL_WAIT_HINT_S - 1) * 1000); });
        expect(screen.queryByText(MODEL_WAIT_HINT)).not.toBeInTheDocument();
        act(() => { vi.advanceTimersByTime(1000); });
        expect(screen.getByText(MODEL_WAIT_HINT)).toBeInTheDocument();
    });

    it('keeps counting while the model-wait status repeats or retries', () => {
        const { rerender } = renderBubble('Reading your request');
        act(() => { vi.advanceTimersByTime(20_000); });
        rerenderWith(rerender, 'The model stopped sending — retrying now (1/1)');
        act(() => { vi.advanceTimersByTime(5_000); });
        rerenderWith(rerender, 'Reading your request');
        act(() => { vi.advanceTimersByTime((MODEL_WAIT_HINT_S - 25) * 1000); });
        expect(screen.getByText(MODEL_WAIT_HINT)).toBeInTheDocument();
    });

    it('shows after a silent retry notice too', () => {
        renderBubble('The model stopped sending — retrying now (1/1)');
        act(() => { vi.advanceTimersByTime(MODEL_WAIT_HINT_S * 1000); });
        expect(screen.getByText(MODEL_WAIT_HINT)).toBeInTheDocument();
    });

    it('never shows while the run waits on the user', () => {
        renderBubble('Reading your request', true);
        act(() => { vi.advanceTimersByTime(10 * MODEL_WAIT_HINT_S * 1000); });
        expect(screen.queryByText(MODEL_WAIT_HINT)).not.toBeInTheDocument();
    });

    it('never shows while a tool runs or the model is producing output', () => {
        for (const label of ['Running run_python', 'Reasoning — 38 words so far', 'Preparing write_file — 1,204 characters so far']) {
            expect(isModelWaitLabel(label)).toBe(false);
        }
        renderBubble('Running run_python');
        act(() => { vi.advanceTimersByTime(10 * MODEL_WAIT_HINT_S * 1000); });
        expect(screen.queryByText(MODEL_WAIT_HINT)).not.toBeInTheDocument();
    });

    it('treats the pre-phase "Thinking..." as a model wait', () => {
        expect(isModelWaitLabel(null)).toBe(true);
        expect(isModelWaitLabel('Thinking...')).toBe(true);
        expect(isModelWaitLabel('Reading your request')).toBe(true);
    });
});

describe('live status line in Arabic', () => {
    afterEach(() => act(() => useChatStore.getState().setLocale('en')));

    it('reads in Arabic, with the seconds counter kept left to right', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        const { container } = renderBubble('Reading your request');
        // The line types itself out one character per tick.
        for (let i = 0; i < 100; i++) act(() => { vi.advanceTimersByTime(100); });
        expect(container.querySelector('.thinking-indicator-text')?.textContent).toContain('جارٍ قراءة طلبك');
        expect(container.querySelector('.thinking-indicator-elapsed bdi[dir="ltr"]')?.textContent).toBe('10s');
    });

    it('says the model is still silent in Arabic', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        renderBubble('Reading your request');
        act(() => { vi.advanceTimersByTime(MODEL_WAIT_HINT_S * 1000); });
        expect(screen.getByRole('status').textContent).toContain('ما زلنا بانتظار النموذج');
    });
});
