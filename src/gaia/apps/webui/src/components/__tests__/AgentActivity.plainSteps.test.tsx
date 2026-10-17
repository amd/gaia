// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
    AgentActivity,
    compactNarration,
    consolidateSteps,
    formatArgsDisplay,
    formatElapsed,
    readableListValue,
} from '../AgentActivity';
import type { AgentStep } from '../../types';
import activityCss from '../AgentActivity.css?raw';
import tokensCss from '../../styles/index.css?raw';

vi.mock('../../services/api');

const PLAN = ['read_file — path=\'a.txt\'', 'read_file — path=\'b.txt\'', 'write_file — path=\'out.md\''];
const NARRATIONS = ['Reading file: a.txt', 'Reading file: b.txt', 'Writing file: out.md'];

function plan(id: number, currentStep: number): AgentStep {
    return {
        id, type: 'plan', label: 'Created plan', planSteps: PLAN, planNarrations: NARRATIONS,
        currentStep, active: false, success: true, timestamp: id,
    };
}

function call(id: number, narration: string, opts: Partial<AgentStep> = {}): AgentStep {
    return {
        id, type: 'tool', label: 'Using tool', tool: 'read_file', narration,
        detail: `path: ${narration}`, active: false, success: true, result: 'done', timestamp: id, ...opts,
    };
}

/** The plan re-sent before each step, as the agent streams it. */
function midPlanTurn(): AgentStep[] {
    return [
        plan(1, 0),
        { id: 2, type: 'thinking', label: 'Thinking', detail: 'Executing step 1 of the plan', active: false, timestamp: 2 },
        call(3, 'Reading file: a.txt'),
        plan(4, 1),
        { id: 5, type: 'thinking', label: 'Thinking', detail: 'Executing step 2 of the plan', active: false, timestamp: 5 },
        call(6, 'Reading file: b.txt', { active: true, success: undefined, result: undefined }),
    ];
}

const checklistItems = () => screen.getAllByRole('listitem').map((li) => li.getAttribute('aria-label'));

describe('plan checklist', () => {
    it('marks each step done, running or not started while the turn runs', () => {
        render(<AgentActivity steps={midPlanTurn()} isActive={true} />);

        expect(checklistItems()).toEqual([
            'Done: Reading file: a.txt',
            'Running: Reading file: b.txt',
            'Not started: Writing file: out.md',
        ]);
        expect(screen.getByText('1 of 3 done')).toBeInTheDocument();
    });

    it('moves on when the plan is re-sent for the next step', () => {
        const steps = [...midPlanTurn()];
        steps[5] = call(6, 'Reading file: b.txt');
        steps.push(plan(7, 2), { ...call(8, 'Writing file: out.md', { tool: 'write_file', active: true, success: undefined, result: undefined }) });
        render(<AgentActivity steps={steps} isActive={true} />);

        expect(checklistItems()).toEqual([
            'Done: Reading file: a.txt',
            'Done: Reading file: b.txt',
            'Running: Writing file: out.md',
        ]);
    });

    it('shows the plan once, not once per step, with no bookkeeping lines', () => {
        const rows = consolidateSteps(midPlanTurn(), true);

        expect(rows.filter((r) => r.type === 'plan')).toHaveLength(1);
        expect(rows.some((r) => r.type === 'thinking')).toBe(false);
    });

    it('marks the step a failed call belonged to as failed once the turn ends', () => {
        const steps = midPlanTurn();
        steps[5] = call(6, 'Reading file: b.txt', { success: false, result: 'error: not found' });
        const [planRow] = consolidateSteps(steps, false).filter((r) => r.type === 'plan');

        expect(planRow.planStatuses).toEqual(['done', 'failed', 'pending']);
    });

    it('marks a step whose call was cut short as stopped, not "not started"', () => {
        const steps = midPlanTurn();
        steps[5] = call(6, 'Reading file: b.txt', { active: false, success: undefined, result: undefined });
        const [planRow] = consolidateSteps(steps, false).filter((r) => r.type === 'plan');

        expect(planRow.planStatuses).toEqual(['done', 'stopped', 'pending']);
    });

    it('gives a replan its own checklist and scores each plan by its own calls', () => {
        const other: AgentStep = {
            id: 10, type: 'plan', label: 'Created plan', planSteps: ['web_search — query=\'x\''],
            planNarrations: ['Searching the web: x'], currentStep: 0, active: false, success: true, timestamp: 10,
        };
        const steps = [
            plan(1, 0), call(2, 'Reading file: a.txt', { success: false, result: 'error: gone' }),
            other, call(11, 'Searching the web: x', { tool: 'web_search', active: true, success: undefined, result: undefined }),
        ];
        const plans = consolidateSteps(steps, true).filter((r) => r.type === 'plan');

        expect(plans.map((p) => p.planStatuses)).toEqual([['failed', 'pending', 'pending'], ['running']]);
        const { container } = render(<AgentActivity steps={steps} isActive={true} />);
        const live = container.querySelector('.agent-activity > .flow-plan') as HTMLElement;
        expect(within(live).getAllByRole('listitem').map((li) => li.getAttribute('aria-label')))
            .toEqual(['Running: Searching the web: x']);
    });

    it('keeps a plan with no known current step as a plain list', () => {
        const legacy: AgentStep = { ...plan(1, 0), currentStep: undefined, planNarrations: undefined };
        const [planRow] = consolidateSteps([legacy], false);

        expect(planRow.planStatuses).toBeUndefined();
    });
});

describe('identical calls', () => {
    const failedRead = (id: number) => call(id, 'Reading web page: go.nps.gov/RainierWeather', {
        tool: 'fetch_page', success: false, result: 'error: timed out',
    });
    const retryThought = (id: number): AgentStep => ({
        id, type: 'thinking', label: 'Thinking', detail: 'Let me try again. ', active: false, timestamp: id,
    });

    it('merges three identical failed reads into one row marked ×3', () => {
        const steps = [failedRead(1), retryThought(2), failedRead(3), retryThought(4), failedRead(5)];
        render(<AgentActivity steps={steps} isActive={false} variant="summary" />);
        fireEvent.click(screen.getByRole('button', { name: 'Expand agent activity' }));

        expect(screen.getAllByText('Reading web page: go.nps.gov/RainierWeather')).toHaveLength(1);
        expect(screen.getByLabelText('3 identical calls')).toHaveTextContent('×3');
        expect(screen.getByText(/· 3 tools$/)).toBeInTheDocument();
    });

    it('keeps calls with different arguments apart', () => {
        const rows = consolidateSteps([call(1, 'Reading file: a.txt'), call(2, 'Reading file: b.txt')], false);

        expect(rows).toHaveLength(2);
    });

    it('keeps a success apart from the failures before it', () => {
        const rows = consolidateSteps([failedRead(1), { ...failedRead(2), success: true, result: 'ok' }], false);

        expect(rows).toHaveLength(2);
    });
});

describe('plain step lines', () => {
    it('shows the narration, not the raw tool name, by default', () => {
        render(<AgentActivity steps={[call(1, 'Reading file: notes.txt', { tool: 'read_file' })]} isActive={false} />);
        fireEvent.click(screen.getByRole('button', { name: 'Expand agent activity' }));

        expect(screen.getByText('Reading file: notes.txt')).toBeInTheDocument();
        expect(screen.queryByText('read_file')).toBeNull();
    });

    it('puts the raw tool name and arguments one click deeper', () => {
        render(<AgentActivity steps={[call(1, 'Reading file: notes.txt', { tool: 'read_file' })]} isActive={false} />);
        fireEvent.click(screen.getByRole('button', { name: 'Expand agent activity' }));
        const row = screen.getByRole('button', { name: /Reading file: notes.txt/ });
        expect(row).toHaveAttribute('aria-expanded', 'false');

        fireEvent.click(row);

        expect(row).toHaveAttribute('aria-expanded', 'true');
        expect(screen.getByText('read_file')).toBeInTheDocument();
        expect(screen.getByText('Arguments')).toBeInTheDocument();
    });

    it('says what a running call is and how long it has run', () => {
        const running = call(1, 'Reading file: notes.txt', { active: true, success: undefined, result: undefined, timestamp: Date.now() - 12_000 });
        const { container } = render(<AgentActivity steps={[running]} isActive={true} />);

        expect(container.querySelector('.agent-summary-live')).toHaveTextContent(/^Reading file: notes\.txt · 1[23]s$/);
    });

    it('says when GAIA is waiting on the model rather than a tool', () => {
        const thought: AgentStep = { id: 1, type: 'thinking', label: 'Thinking', detail: 'Hmm', active: true, timestamp: Date.now() - 5_000 };
        const { container } = render(<AgentActivity steps={[thought]} isActive={true} />);

        expect(container.querySelector('.agent-summary-live')).toHaveTextContent(/^Waiting on the model · [56]s$/);
    });

    it('shows no live line once the turn is over', () => {
        const { container } = render(<AgentActivity steps={[call(1, 'Reading file: a.txt')]} isActive={false} />);

        expect(container.querySelector('.agent-summary-live')).toBeNull();
    });

    it('shows a failed call\'s reason on its row', () => {
        const failed = call(1, 'Reading file: a.txt', { success: false, result: 'error: not found' });
        render(<AgentActivity steps={[failed]} isActive={false} />);
        const row = screen.getByRole('button', { name: /Reading file: a.txt/ });

        expect(within(row).getByText('error: not found')).toBeInTheDocument();
    });
});

describe('compactNarration', () => {
    it.each([
        ['Reading web page: https://www.go.nps.gov/RainierWeather/', 'Reading web page: go.nps.gov/RainierWeather'],
        ['Reading web page: https://example.com/docs/guides/setup/install/windows-11-pro', 'Reading web page: example.com/…/windows-11-pro'],
        ['Reading file: C:\\Users\\me\\Documents\\Projects\\minutes\\2025-08-28.html', 'Reading file: …\\minutes\\2025-08-28.html'],
        ['Running command: git log --oneline -5', 'Running command: git log --oneline -5'],
        ['Checking your installed skills', 'Checking your installed skills'],
    ])('%s -> %s', (text, expected) => {
        expect(compactNarration(text)).toBe(expected);
    });
});

describe('list arguments', () => {
    it('keeps a list argument on one readable row', () => {
        render(<>{formatArgsDisplay("fields: ['owner', 'task', 'due', 'status'], file: minutes.html")}</>);

        expect(screen.getByText('owner, task, due, status')).toBeInTheDocument();
        expect(screen.getByText('minutes.html')).toBeInTheDocument();
    });

    it.each([
        ["['owner', 'task']", 'owner, task'],
        ['["a"]', 'a'],
        ['plain text', 'plain text'],
    ])('%s reads as %s', (value, expected) => {
        expect(readableListValue(value)).toBe(expected);
    });
});

it('formats elapsed time', () => {
    expect(formatElapsed(8.7)).toBe('8s');
    expect(formatElapsed(125)).toBe('2m 05s');
});

it('keeps every font size in the activity view at 12px or more', () => {
    const sizes = [...activityCss.matchAll(/font-size:\s*([\d.]+)px/g)].map((m) => Number(m[1]));

    expect(sizes.length).toBeGreaterThan(0);
    expect(sizes.filter((px) => px < 12)).toEqual([]);
});

describe('reasoning', () => {
    const thought = (active: boolean): AgentStep => ({
        id: 1, type: 'thinking', label: 'Thinking', active, timestamp: Date.now(),
        detail: 'Consult the rules, then search the web for the forecast.',
    });

    it('is folded away once the turn is over', () => {
        const { container } = render(<AgentActivity steps={[thought(false), call(2, 'Reading file: a.txt')]} isActive={false} />);
        fireEvent.click(screen.getByRole('button', { name: 'Expand agent activity' }));
        const toggle = screen.getByRole('button', { name: /Reasoning/ });

        expect(toggle).toHaveAttribute('aria-expanded', 'false');
        expect(container.querySelector('.flow-thought-hacker')).toBeNull();

        fireEvent.click(toggle);
        expect(container.querySelector('.flow-thought-hacker')).toHaveTextContent('Consult the rules');
    });

    it('streams in view while the model is thinking, then folds when the turn ends', () => {
        const { container, rerender } = render(<AgentActivity steps={[thought(true)]} isActive={true} />);
        fireEvent.click(screen.getByRole('button', { name: 'Expand agent activity' }));
        expect(container.querySelector('.flow-thought-hacker')).not.toBeNull();

        rerender(<AgentActivity steps={[thought(false)]} isActive={false} />);
        expect(container.querySelector('.flow-thought-hacker')).toBeNull();
    });

    it('meets WCAG AA contrast in the light and dark themes', () => {
        const [light, dark] = tokensCss.split('[data-theme="dark"] {');
        const token = (block: string, name: string) => {
            const m = new RegExp(`${name}:\\s*#([0-9a-fA-F]{6})`).exec(block);
            if (!m) throw new Error(`${name} not found`);
            return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
        };
        const lum = (rgb: number[]) => {
            const [r, g, b] = rgb.map((v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
            return 0.2126 * r + 0.7152 * g + 0.0722 * b;
        };
        const ratio = (a: number[], b: number[]) => {
            const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
            return (hi + 0.05) / (lo + 0.05);
        };
        // The reasoning body and toggle are drawn in these tokens (asserted below).
        expect(activityCss).toMatch(/\.flow-thought-hacker \{[^}]*color: var\(--text-secondary\)[^}]*background: var\(--bg-recessed\)/);
        for (const block of [light, dark]) {
            expect(ratio(token(block, '--text-secondary'), token(block, '--bg-recessed'))).toBeGreaterThanOrEqual(4.5);
            // The toggle's word count sits on the panel background.
            expect(ratio(token(block, '--text-muted'), token(block, '--bg-secondary'))).toBeGreaterThanOrEqual(4.5);
        }
    });
});

describe('expanded view order', () => {
    it('drops progress notes from a finished turn', () => {
        const status: AgentStep = { id: 1, type: 'status', label: 'Sending to model...', active: false, timestamp: 1 };
        render(<AgentActivity steps={[status, call(2, 'Reading file: a.txt')]} isActive={false} />);

        expect(screen.queryByText('Sending to model...')).toBeNull();
    });

    it('leads with the steps and puts the filter after them, only for busy turns', () => {
        const few = [1, 2, 3].map((i) => call(i, `Reading file: ${i}.txt`));
        const { container, unmount } = render(<AgentActivity steps={few} isActive={false} />);
        fireEvent.click(screen.getByRole('button', { name: 'Expand agent activity' }));
        expect(screen.queryByLabelText('Filter by tool name')).toBeNull();
        unmount();

        const many = [1, 2, 3, 4, 5, 6].map((i) => call(i, `Reading file: ${i}.txt`));
        const view = render(<AgentActivity steps={many} isActive={false} />);
        fireEvent.click(screen.getByRole('button', { name: 'Expand agent activity' }));
        const wrap = view.container.querySelector('.agent-flow-wrap')!;
        expect(wrap.firstElementChild).toHaveClass('agent-flow');
        expect(wrap.lastElementChild).toHaveClass('activity-filter-bar');
        void container;
    });
});
