// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import type { AgentStep } from '../../types';

vi.mock('../../services/api');

function call(id: number, opts: Partial<AgentStep> = {}): AgentStep {
    return {
        id, type: 'tool', label: 'Using tool', tool: 'read_file', narration: `Reading file: f${id}.txt`,
        detail: `path: f${id}.txt`, active: false, success: true, result: 'done', timestamp: id, ...opts,
    };
}

function note(id: number, text: string): AgentStep {
    return { id, type: 'commentary', label: 'Commentary', detail: text, active: false, timestamp: id };
}

function thought(id: number, text: string, active = false): AgentStep {
    return { id, type: 'thinking', label: 'Thinking', detail: text, active, timestamp: id };
}

afterEach(() => vi.useRealTimers());

describe('a long turn', () => {
    it('renders and ticks hundreds of live steps without stalling', () => {
        vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'] });
        const steps = Array.from({ length: 600 }, (_, i) => call(i + 1));
        steps[steps.length - 1] = call(600, { active: true, success: undefined, result: undefined });

        const t0 = performance.now();
        const { container, rerender } = render(<AgentActivity steps={steps} isActive={true} running={true} />);
        for (let tick = 0; tick < 5; tick++) act(() => { vi.advanceTimersByTime(1000); });
        rerender(<AgentActivity steps={[...steps, call(601, { active: true, success: undefined })]} isActive={true} running={true} />);
        const elapsed = performance.now() - t0;

        expect(container.querySelectorAll('.flow-steps').length).toBeGreaterThan(0);
        expect(elapsed).toBeLessThan(5000);
    });

    it('folds a finished run of hundreds of steps into one line', () => {
        const steps = [...Array.from({ length: 400 }, (_, i) => call(i + 1)), note(401, 'Done reading.')];
        const { container } = render(<AgentActivity steps={steps} isActive={false} running={false} answer="All files read." />);

        const group = container.querySelector('.flow-steps');
        expect(group?.classList.contains('folded')).toBe(true);
        expect(container.querySelectorAll('.flow-steps-rows').length).toBe(0);
    });
});

describe('streamed text cannot inject markup', () => {
    const payload = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script><iframe src="javascript:alert(1)"></iframe>';

    it('renders a note with raw HTML as inert text', () => {
        const { container } = render(
            <AgentActivity steps={[note(1, `Checking ${payload}`), call(2)]} isActive={false} running={false} />,
        );
        expect(container.textContent).toContain('Checking');
        expect(container.querySelector('img, script, iframe')).toBeNull();
        expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    });

    it('renders reasoning with raw HTML as inert text when opened', () => {
        const { container } = render(
            <AgentActivity steps={[thought(1, `Plan ${payload}`), call(2), call(3)]} isActive={false} running={false} />,
        );
        for (const btn of screen.getAllByRole('button')) fireEvent.click(btn);
        for (const btn of screen.queryAllByRole('button', { name: /thought/i })) fireEvent.click(btn);
        expect(container.textContent).toContain('<script>');
        expect(container.querySelector('img, script, iframe')).toBeNull();
    });

    it('drops a javascript: link in a note', () => {
        const { container } = render(
            <AgentActivity steps={[note(1, '[open](javascript:alert(1))'), call(2)]} isActive={false} running={false} />,
        );
        for (const a of container.querySelectorAll('a')) {
            expect(a.getAttribute('href') ?? '').not.toMatch(/^javascript:/i);
        }
    });
});
