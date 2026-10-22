// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Long content opens as a preview: a pasted log, a 300-line code block and a
 * long tool output each collapse behind a real toggle, and Copy still copies
 * the whole text.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MessageBubble, RenderedContent } from '../MessageBubble';
import { AgentActivity } from '../AgentActivity';
import { parseTruncationEnvelope } from '../LongContent';
import type { AgentStep, Message } from '../../types';

vi.mock('../../services/api');

const base: Message = {
    id: 1,
    session_id: 'sess-1',
    role: 'user',
    content: '',
    created_at: '2026-07-16T00:00:00.000Z',
    rag_sources: null,
};

function numbered(n: number, prefix = 'line'): string {
    return Array.from({ length: n }, (_, i) => `${prefix} ${i + 1}`).join('\n');
}

let writeText: ReturnType<typeof vi.fn>;
beforeEach(() => {
    writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
});
afterEach(() => {
    vi.restoreAllMocks();
});

describe('long user messages', () => {
    it('collapses a 700-line paste and expands it on click', () => {
        const { container } = render(<MessageBubble message={{ ...base, content: numbered(700) }} />);

        const body = container.querySelector('.msg-collapsible')!;
        expect(body).toHaveClass('is-collapsed');
        const toggle = screen.getByRole('button', { name: 'Show all (700 lines)' });
        expect(toggle).toHaveAttribute('aria-expanded', 'false');
        expect(toggle).toHaveAttribute('aria-controls', body.id);

        fireEvent.click(toggle);

        expect(body).not.toHaveClass('is-collapsed');
        expect(screen.getByRole('button', { name: 'Show less' })).toHaveAttribute('aria-expanded', 'true');

        fireEvent.click(screen.getByRole('button', { name: 'Show less' }));
        expect(body).toHaveClass('is-collapsed');
        expect(screen.getByRole('button', { name: 'Show all (700 lines)' })).toHaveAttribute('aria-expanded', 'false');
    });

    it('counts characters when the length is in one long line', () => {
        render(<MessageBubble message={{ ...base, content: 'x'.repeat(60_000) }} />);

        expect(screen.getByRole('button', { name: 'Show all (60,000 characters)' })).toBeInTheDocument();
    });

    it('leaves a short message alone', () => {
        const { container } = render(<MessageBubble message={{ ...base, content: numbered(15) }} />);

        expect(container.querySelector('.msg-collapsible')).toBeNull();
        expect(container.querySelector('.expand-toggle')).toBeNull();
        expect(container.textContent).toContain('line 15');
    });

    it('keeps the whole message in the page and copies all of it', () => {
        const content = numbered(700);
        const { container } = render(<MessageBubble message={{ ...base, content }} />);

        expect(container.querySelector('.msg-collapsible')!.textContent).toContain('line 700');
        fireEvent.click(screen.getByRole('button', { name: 'Copy message' }));
        expect(writeText).toHaveBeenCalledWith(content);
    });

    it('resends the full text', () => {
        const content = numbered(700);
        const onResend = vi.fn();
        render(<MessageBubble message={{ ...base, content }} onResend={onResend} />);

        fireEvent.click(screen.getByRole('button', { name: 'Resend message' }));
        expect(onResend.mock.calls[0][0].content).toBe(content);
    });
});

describe('long code blocks', () => {
    const fence = (n: number) => '```python\n' + numbered(n, 'x =') + '\n```';
    const answer = (content: string) => ({ ...base, role: 'assistant' as const, content });

    it('collapses a 300-line block and expands it on click', () => {
        const { container } = render(<MessageBubble message={answer(fence(300))} />);

        const block = container.querySelector('.code-block')!;
        expect(block).toHaveClass('is-collapsed');
        const toggle = screen.getByRole('button', { name: 'Expand (300 lines)' });
        expect(toggle).toHaveAttribute('aria-expanded', 'false');
        expect(toggle).toHaveAttribute('aria-controls', block.querySelector('pre')!.id);

        fireEvent.click(toggle);

        expect(block).not.toHaveClass('is-collapsed');
        expect(screen.getByRole('button', { name: 'Collapse' })).toHaveAttribute('aria-expanded', 'true');
    });

    it('leaves a 25-line block alone', () => {
        const { container } = render(<MessageBubble message={answer(fence(25))} />);

        expect(container.querySelector('.code-block')).not.toHaveClass('is-collapsed');
        expect(container.querySelector('.expand-toggle')).toBeNull();
    });

    it('copies the whole block while collapsed', () => {
        render(<MessageBubble message={answer(fence(300))} />);

        fireEvent.click(screen.getByRole('button', { name: 'Copy code' }));
        expect(writeText).toHaveBeenCalledWith(numbered(300, 'x ='));
    });

    it('caps a streaming block once it fills the preview, and keeps an expand across tokens', () => {
        const partial = (n: number) => '```python\n' + numbered(n, 'x =');
        const { container, rerender } = render(<RenderedContent content={partial(20)} isStreaming />);
        expect(container.querySelector('.code-block')).not.toHaveClass('is-collapsed');

        rerender(<RenderedContent content={partial(21)} isStreaming />);
        expect(container.querySelector('.code-block')).toHaveClass('is-collapsed');

        fireEvent.click(screen.getByRole('button', { name: 'Expand (21 lines)' }));
        rerender(<RenderedContent content={partial(40)} isStreaming />);
        expect(container.querySelector('.code-block')).not.toHaveClass('is-collapsed');
        expect(screen.getByRole('button', { name: 'Collapse' })).toHaveAttribute('aria-expanded', 'true');
    });
});

describe('long step output', () => {
    const paths = Array.from({ length: 400 }, (_, i) => `C:\\Windows\\System32\\drivers\\d${i}.sys`).join('\n');
    const envelope = JSON.stringify({
        truncated: true,
        original_chars: 26249,
        omitted_chars: 17548,
        head: paths.slice(0, 4351),
        tail: paths.slice(-4350),
    });

    function renderStep(result: string) {
        const steps: AgentStep[] = [{
            id: 1, type: 'tool', label: 'Ran command', tool: 'run_shell_command',
            result, active: false, success: true, timestamp: 1,
        }];
        const view = render(<AgentActivity steps={steps} isActive={false} variant="summary" />);
        fireEvent.click(screen.getByRole('button', { name: 'Expand agent activity' }));
        fireEvent.click(view.container.querySelector('.flow-tool-header')!);
        return view;
    }

    it('shows a truncation envelope as plain text with real line breaks and a note', () => {
        const { container } = renderStep(envelope);

        const content = container.querySelector('.step-detail-result .detail-result-content')!;
        expect(content.textContent).not.toContain('"truncated"');
        expect(content.textContent).not.toContain('\\n');
        expect(content.textContent!.startsWith('C:\\Windows\\System32\\drivers\\d0.sys\nC:\\Windows')).toBe(true);
        expect(content.querySelector('a, .tool-result-path')).toBeNull();
        expect(container.querySelector('.detail-result-note')!.textContent).toBe(
            'Showing the first 4,351 and last 4,350 characters of 26,249 (17,548 omitted).',
        );
    });

    it('caps a long output behind an Expand toggle', () => {
        const { container } = renderStep(envelope);

        const content = container.querySelector('.detail-result-content')!;
        expect(content).toHaveClass('is-collapsed');
        const toggle = container.querySelector('.step-expand-toggle')!;
        expect(toggle).toHaveAttribute('aria-expanded', 'false');
        expect(toggle.textContent).toMatch(/^Expand \(\d+ lines\)$/);

        fireEvent.click(toggle);
        expect(content).not.toHaveClass('is-collapsed');
        expect(toggle).toHaveAttribute('aria-expanded', 'true');
    });

    it('reads an envelope whose JSON was cut short', () => {
        const env = parseTruncationEnvelope(envelope.slice(0, 300));

        expect(env).not.toBeNull();
        expect(env!.head.startsWith('C:\\Windows\\System32\\drivers\\d0.sys\nC:\\')).toBe(true);
        expect(env!.originalChars).toBe(26249);
        expect(env!.omittedChars).toBe(17548);
    });

    it('ignores JSON that is not an envelope', () => {
        expect(parseTruncationEnvelope('{"truncated": false, "head": "a"}')).toBeNull();
        expect(parseTruncationEnvelope('{"files": []}')).toBeNull();
        expect(parseTruncationEnvelope('plain text')).toBeNull();
    });

    it('still links the path in a short result', () => {
        const { container } = renderStep('Saved to C:\\Users\\me\\out.txt');

        expect(container.querySelector('.detail-result-content')).not.toHaveClass('is-collapsed');
        expect(container.querySelector('.step-expand-toggle')).toBeNull();
        expect(container.querySelector('.detail-result-content .tool-result-path')).not.toBeNull();
    });
});
