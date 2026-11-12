// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The live status line shimmers only while the model works and shows its whole label at once. */

import { render } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../types';
import bubbleCss from '../MessageBubble.css?raw';

const streaming: Message = {
    id: -1,
    session_id: 's',
    role: 'assistant',
    content: '',
    created_at: '2026-10-16T00:00:00Z',
    rag_sources: null,
};

function renderBubble(props: { awaitingUser?: boolean; active?: boolean } = {}) {
    return render(
        <MessageBubble
            message={streaming}
            isStreaming
            agentSteps={[]}
            agentStepsActive={props.active ?? true}
            liveStatus="Reading your request"
            awaitingUser={props.awaitingUser ?? false}
        />,
    );
}

function mockReducedMotion(reduce: boolean) {
    vi.stubGlobal('matchMedia', (query: string) => ({
        matches: reduce && query === '(prefers-reduced-motion: reduce)',
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
    }));
}

const indicator = (c: HTMLElement) => c.querySelector('.thinking-indicator');
const label = (c: HTMLElement) => c.querySelector('.thinking-indicator-text')?.textContent;

beforeEach(() => {
    vi.useFakeTimers();
});

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

describe('live status motion', () => {
    it('marks the line live while the model works', () => {
        mockReducedMotion(false);
        const { container } = renderBubble();
        expect(indicator(container)).toHaveClass('is-live');
    });

    it('is not live while GAIA waits on the user', () => {
        mockReducedMotion(false);
        const { container } = renderBubble({ awaitingUser: true });
        expect(indicator(container)).not.toHaveClass('is-live');
    });

    it('shows the whole label at once, in the UI font, with no typing cursor', () => {
        mockReducedMotion(false);
        const { container } = renderBubble();
        expect(label(container)).toBe('Reading your request');
        expect(container.querySelector('.thinking-indicator .cursor')).toBeNull();
        expect(bubbleCss).toMatch(/\.thinking-indicator \{[^}]*font-family: var\(--font-sans\)/);
    });

    it('drops the line as soon as the model stops', () => {
        mockReducedMotion(false);
        const { container, rerender } = renderBubble();
        rerender(
            <MessageBubble message={{ ...streaming, content: 'Done.' }} isStreaming agentSteps={[]}
                agentStepsActive={false} liveStatus="Reading your request" />,
        );
        expect(indicator(container)).toBeNull();
    });

    it('shows the whole label on its first render under reduced motion', () => {
        mockReducedMotion(true);
        const html = renderToStaticMarkup(
            <MessageBubble message={streaming} isStreaming agentSteps={[]} agentStepsActive
                liveStatus="Reading your request" />,
        );
        expect(html).toContain('>Reading your request<');
    });

    it('removes the line without erasing it under reduced motion', () => {
        mockReducedMotion(true);
        const { container, rerender } = renderBubble();
        rerender(
            <MessageBubble message={{ ...streaming, content: 'Done.' }} isStreaming agentSteps={[]}
                agentStepsActive={false} liveStatus="Reading your request" />,
        );
        expect(indicator(container)).toBeNull();
    });
});
