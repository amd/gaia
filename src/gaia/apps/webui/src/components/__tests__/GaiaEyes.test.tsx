// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The eyes step through their sheet from script, react once per message, and hold still under reduced motion. */

import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GaiaEyes } from '../GaiaEyes';
import { MessageBubble } from '../MessageBubble';
import { useChatStore } from '../../stores/chatStore';
import type { Message } from '../../types';
import { EYES_ANIMATIONS, EYES_CELL, EYES_VARIANTS } from '../gaiaEyesSheet';
import { LONGEST_MOOD_MS } from '../../utils/gaiaEyes';

type Name = keyof typeof EYES_ANIMATIONS;

function mockReducedMotion(reduce: boolean) {
    vi.stubGlobal('matchMedia', (query: string) => ({
        matches: reduce && query === '(prefers-reduced-motion: reduce)',
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
    }));
}

const eyes = (c: HTMLElement) => c.querySelector<HTMLElement>('.gaia-eyes')!;
const shown = (c: HTMLElement) => eyes(c).style.getPropertyValue('mask-position');
/** The variation being played, which the eyes pick themselves. */
const variant = (c: HTMLElement) => eyes(c).dataset.variant as Name;
const at = (name: Name, frame: number) => `${-frame * EYES_CELL}px ${-EYES_ANIMATIONS[name].row * EYES_CELL}px`;
const frameMs = (name: Name) => 1000 / EYES_ANIMATIONS[name].fps;
const lengthMs = (name: Name) => EYES_ANIMATIONS[name].frames * frameMs(name);
/** Long enough for any variation of a one-shot state to finish. */
const longestMs = (state: keyof typeof EYES_VARIANTS) => Math.max(...EYES_VARIANTS[state].map(lengthMs));

beforeEach(() => {
    vi.useFakeTimers();
    mockReducedMotion(false);
});

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

describe('GaiaEyes', () => {
    it('is decoration: hidden from assistive tech, the status text carries the meaning', () => {
        const { container } = render(<GaiaEyes phase="think" />);
        expect(eyes(container)).toHaveAttribute('aria-hidden', 'true');
        expect(eyes(container).textContent).toBe('');
    });

    it('steps through the working animation frame by frame', () => {
        const { container } = render(<GaiaEyes phase="read" />);
        const playing = variant(container);
        expect(eyes(container).dataset.eyes).toBe('read');
        expect(EYES_VARIANTS.read).toContain(playing);
        expect(shown(container)).toBe(at(playing, 0));
        act(() => { vi.advanceTimersByTime(frameMs(playing) * 3); });
        expect(shown(container)).toBe(at(playing, 3));
    });

    it('plays a different variation each time a working state comes round', () => {
        const { container } = render(<GaiaEyes phase="think" />);
        const seen = [variant(container)];
        for (let lap = 0; lap < 6; lap++) {
            act(() => { vi.advanceTimersByTime(lengthMs(variant(container))); });
            expect(shown(container)).toBe(at(variant(container), 0));
            expect(variant(container)).not.toBe(seen[seen.length - 1]);
            seen.push(variant(container));
        }
        expect(seen.every((name) => (EYES_VARIANTS.think as readonly Name[]).includes(name))).toBe(true);
    });

    it('does not play a reaction the same way twice in a row', () => {
        const first = render(<GaiaEyes phase="think" reaction={{ mood: 'sad', key: 'sad-once' }} />);
        const before = variant(first.container);
        first.unmount();
        const second = render(<GaiaEyes phase="think" reaction={{ mood: 'sad', key: 'sad-twice' }} />);
        expect(EYES_VARIANTS.sad).toContain(variant(second.container));
        expect(variant(second.container)).not.toBe(before);
    });

    it('starts the new state from its first frame when the phase changes', () => {
        const { container, rerender } = render(<GaiaEyes phase="think" />);
        act(() => { vi.advanceTimersByTime(frameMs('think') * 5); });
        rerender(<GaiaEyes phase="tool" />);
        expect(eyes(container).dataset.eyes).toBe('tool');
        expect(shown(container)).toBe(at(variant(container), 0));
    });

    it('plays waiting-on-you once and rests on its last frame', () => {
        const { container } = render(<GaiaEyes phase="wait" />);
        act(() => { vi.advanceTimersByTime(lengthMs('wait') * 3); });
        expect(shown(container)).toBe(at('wait', EYES_ANIMATIONS.wait.frames - 1));
    });

    it('reacts to the message first, then gets to work', () => {
        const { container } = render(<GaiaEyes phase="think" reaction={{ mood: 'sad', key: 'reacts-first' }} />);
        expect(eyes(container).dataset.eyes).toBe('sad');
        act(() => { vi.advanceTimersByTime(longestMs('sad') + 1); });
        expect(eyes(container).dataset.eyes).toBe('think');
        expect(shown(container)).toBe(at(variant(container), 0));
    });

    it('does not replay a reaction to the same message after a remount', () => {
        const reaction = { mood: 'angry', key: 'same-message' } as const;
        const first = render(<GaiaEyes phase="think" reaction={reaction} />);
        expect(eyes(first.container).dataset.eyes).toBe('angry');
        act(() => { vi.advanceTimersByTime(longestMs('angry') + 1); });
        first.unmount();
        const second = render(<GaiaEyes phase="think" reaction={reaction} />);
        expect(eyes(second.container).dataset.eyes).toBe('think');
    });

    it('carries a reaction on when the status line is rebuilt partway through it', () => {
        const reaction = { mood: 'angry', key: 'rebuilt-mid-reaction' } as const;
        const first = render(<GaiaEyes phase="think" reaction={reaction} />);
        const playing = variant(first.container);
        act(() => { vi.advanceTimersByTime(4 * frameMs(playing) + 1); });
        first.unmount();
        const second = render(<GaiaEyes phase="think" reaction={reaction} />);
        expect(eyes(second.container).dataset.eyes).toBe('angry');
        expect(variant(second.container)).toBe(playing);
        expect(shown(second.container)).toBe(at(playing, 4));
        act(() => { vi.advanceTimersByTime(lengthMs(playing)); });
        expect(eyes(second.container).dataset.eyes).toBe('think');
    });

    it('reacts again when the next message arrives', () => {
        const { container, rerender } = render(<GaiaEyes phase="think" reaction={{ mood: 'happy', key: 'first-message' }} />);
        act(() => { vi.advanceTimersByTime(longestMs('happy') + 1); });
        rerender(<GaiaEyes phase="think" reaction={{ mood: 'love', key: 'second-message' }} />);
        expect(eyes(container).dataset.eyes).toBe('love');
    });

    it('shows one still per state under reduced motion and never steps', () => {
        mockReducedMotion(true);
        const { container } = render(<GaiaEyes phase="think" />);
        expect(shown(container)).toBe(at('think', EYES_ANIMATIONS.think.pose));
        act(() => { vi.advanceTimersByTime(lengthMs('think')); });
        expect(shown(container)).toBe(at('think', EYES_ANIMATIONS.think.pose));
    });

    it('holds a reaction still for its length under reduced motion, then shows the work', () => {
        mockReducedMotion(true);
        const { container } = render(<GaiaEyes phase="read" reaction={{ mood: 'surprised', key: 'still-reaction' }} />);
        expect(shown(container)).toBe(at('surprised', EYES_ANIMATIONS.surprised.pose));
        act(() => { vi.advanceTimersByTime(lengthMs('surprised') + 1); });
        expect(eyes(container).dataset.eyes).toBe('read');
    });
});

describe('the eyes in the live status line', () => {
    const message = (over: Partial<Message>): Message => ({
        id: 1, session_id: 's', role: 'user', content: '', created_at: '2026-11-19T00:00:00Z', rag_sources: null, ...over,
    });
    const streaming = message({ id: -1, role: 'assistant' });

    function renderTurn(asked: string, props: { liveStatus?: string | null; awaitingUser?: boolean; answer?: string } = {}) {
        useChatStore.setState({ messages: [message({ content: asked, created_at: `2026-11-19T00:00:00Z#${asked}` })] });
        const bubble = () => (
            <MessageBubble
                message={props.answer ? { ...streaming, content: props.answer } : streaming}
                isStreaming
                agentSteps={[]}
                agentStepsActive={!props.answer}
                liveStatus={props.liveStatus ?? 'Thinking...'}
                awaitingUser={props.awaitingUser ?? false}
            />
        );
        return render(bubble());
    }

    afterEach(() => {
        useChatStore.setState({ messages: [], gaiaMood: null });
    });

    it("play GAIA's own mood when she names one, over the reaction to the message", () => {
        const { container } = renderTurn('thank you so much, that fixed it');
        expect(eyes(container).dataset.eyes).toBe('happy');
        act(() => { useChatStore.getState().setGaiaMood('proud'); });
        expect(eyes(container).dataset.eyes).toBe('proud');
        act(() => { vi.advanceTimersByTime(longestMs('proud') + 1); });
        expect(eyes(container).dataset.eyes).toBe('think');
    });

    it('ignore a mood word they cannot draw', () => {
        const { container } = renderTurn('summarize this report');
        act(() => { useChatStore.getState().setGaiaMood('calm'); });
        expect(useChatStore.getState().gaiaMood).toBeNull();
        expect(eyes(container).dataset.eyes).toBe('think');
    });

    it('stay beside an answer that is already streaming, just long enough to play her mood', () => {
        const { container } = renderTurn('my dog died this morning', { answer: 'I am so sorry.' });
        expect(container.querySelector('.thinking-indicator')).toBeNull();
        act(() => { useChatStore.getState().setGaiaMood('sad'); });
        expect(eyes(container).dataset.eyes).toBe('sad');
        expect(container.querySelector('.thinking-indicator-text')).toBeNull();
        act(() => { vi.advanceTimersByTime(LONGEST_MOOD_MS + 100); });
        expect(container.querySelector('.thinking-indicator')).toBeNull();
    });

    it('finish her mood beside a short answer that ended first, from where it had got to', () => {
        useChatStore.setState({ messages: [message({ content: 'my dog died this morning' })] });
        act(() => { useChatStore.getState().setGaiaMood('sad'); });
        act(() => { vi.advanceTimersByTime(500); });
        // The reply is over: the finished message takes the place of the streaming one.
        useChatStore.getState().clearAgentSteps();
        const { container } = render(
            <MessageBubble message={message({ id: 2, role: 'assistant', content: 'I am so sorry.' })} latest />,
        );
        expect(eyes(container).dataset.eyes).toBe('sad');
        expect(shown(container)).toBe(at(variant(container), 5));
        act(() => { vi.advanceTimersByTime(LONGEST_MOOD_MS); });
        expect(container.querySelector('.gaia-eyes')).toBeNull();
    });

    it('show no eyes beside an older answer', () => {
        act(() => { useChatStore.getState().setGaiaMood('sad'); });
        const { container } = render(
            <MessageBubble message={message({ id: 2, role: 'assistant', content: 'I am so sorry.' })} />,
        );
        expect(container.querySelector('.gaia-eyes')).toBeNull();
    });

    it("react to the next message once GAIA's last mood has played out", () => {
        act(() => { useChatStore.getState().setGaiaMood('sad'); });
        act(() => { vi.advanceTimersByTime(LONGEST_MOOD_MS + 100); });
        const { container } = renderTurn('thank you so much, that fixed it');
        expect(eyes(container).dataset.eyes).toBe('happy');
    });

    it('react at once to bad news about a pet', () => {
        const { container } = renderTurn('my dog died this morning');
        expect(eyes(container).dataset.eyes).toBe('sad');
    });

    it('sit before the status text and follow what GAIA is doing', () => {
        const { container } = renderTurn('summarize this report', { liveStatus: 'Reading page 2 of 9…' });
        const line = container.querySelector('.thinking-indicator')!;
        expect(line.firstElementChild).toBe(eyes(container));
        expect(eyes(container).dataset.eyes).toBe('read');
    });

    it('wait when GAIA needs an answer', () => {
        const { container } = renderTurn('delete the old logs', { awaitingUser: true });
        expect(eyes(container).dataset.eyes).toBe('wait');
    });

    it('react to how the message sounded before showing the work', () => {
        const { container } = renderTurn('thank you so much, that fixed it');
        expect(eyes(container).dataset.eyes).toBe('happy');
        act(() => { vi.advanceTimersByTime(longestMs('happy') + 1); });
        expect(eyes(container).dataset.eyes).toBe('think');
    });
});
