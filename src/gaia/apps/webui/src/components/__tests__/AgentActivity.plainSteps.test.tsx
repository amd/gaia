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
    thoughtDurations,
    thoughtLabel,
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
        render(<AgentActivity steps={steps} isActive={false} />);
        fireEvent.click(screen.getByRole('button', { name: /^Read web page ×3$/ }));

        expect(screen.getAllByText('Reading web page: go.nps.gov/RainierWeather')).toHaveLength(1);
        expect(screen.getByLabelText('3 identical calls')).toHaveTextContent('×3');
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

        expect(screen.getByText('Reading file: notes.txt')).toBeInTheDocument();
        expect(screen.queryByText('read_file')).toBeNull();
    });

    it('puts the raw tool name and arguments one click deeper', () => {
        render(<AgentActivity steps={[call(1, 'Reading file: notes.txt', { tool: 'read_file' })]} isActive={false} />);
        const row = screen.getByRole('button', { name: /Reading file: notes.txt/ });
        expect(row).toHaveAttribute('aria-expanded', 'false');

        fireEvent.click(row);

        expect(row).toHaveAttribute('aria-expanded', 'true');
        expect(screen.getByText('read_file')).toBeInTheDocument();
        expect(screen.getByText('Arguments')).toBeInTheDocument();
    });

    it('shows a running call in view with its timer, and no live line beside it', () => {
        const running = call(1, 'Reading file: notes.txt', { active: true, success: undefined, result: undefined, timestamp: Date.now() - 12_000 });
        render(<AgentActivity steps={[running]} isActive={true} liveLine={<span>Thinking</span>} />);

        const row = screen.getByRole('button', { name: /Reading file: notes.txt/ });
        expect(within(row).getByText('12s')).toBeInTheDocument();
        expect(screen.queryByText('Thinking')).toBeNull();
    });

    it('shows the live line between steps, where the next one will appear', () => {
        const { container } = render(
            <AgentActivity steps={[call(1, 'Reading file: notes.txt')]} isActive={false} running liveLine={<span>Thinking</span>} />,
        );

        expect(container.querySelector('.agent-activity')!.lastElementChild).toHaveTextContent('Thinking');
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
        fireEvent.click(screen.getByRole('button', { name: /^Read file$/ }));
        const toggle = screen.getByRole('button', { name: /Thought/ });

        expect(toggle).toHaveAttribute('aria-expanded', 'false');
        expect(container.querySelector('.flow-thought-hacker')).toBeNull();

        fireEvent.click(toggle);
        expect(container.querySelector('.flow-thought-hacker')).toHaveTextContent('Consult the rules');
    });

    it('streams in view while the model is thinking, then folds when the turn ends', () => {
        const { container, rerender } = render(<AgentActivity steps={[thought(true)]} isActive={true} />);
        expect(container.querySelector('.flow-thought-hacker')).toHaveTextContent('Consult the rules');

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
            // Live reasoning is previewed in muted text on the chat canvas.
            expect(ratio(token(block, '--text-muted'), token(block, '--bg-primary'))).toBeGreaterThanOrEqual(4.5);
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
        const { unmount } = render(<AgentActivity steps={few} isActive={false} />);
        fireEvent.click(screen.getByRole('button', { name: /^Read file ×3$/ }));
        expect(screen.queryByLabelText('Filter by tool name')).toBeNull();
        unmount();

        const many = [1, 2, 3, 4, 5, 6].map((i) => call(i, `Reading file: ${i}.txt`));
        const view = render(<AgentActivity steps={many} isActive={false} />);
        expect(screen.queryByLabelText('Filter by tool name')).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: /^Read file ×6$/ }));
        const activity = view.container.querySelector('.agent-activity')!;
        expect(activity.firstElementChild).toHaveClass('agent-timeline');
        expect(activity.lastElementChild).toHaveClass('activity-filter-bar');
    });
});

describe('background memory calls', () => {
    const memory = (id: number, success: boolean): AgentStep =>
        call(id, 'Remembering: owner', { tool: 'remember', success });

    it('stay out of the rows and do not split identical calls', () => {
        const rows = consolidateSteps([
            call(1, 'Reading file: a.txt'), memory(2, true), call(3, 'Reading file: a.txt'),
        ], false);

        expect(rows.map((r) => [r.tool, r.count ?? 1])).toEqual([['read_file', 2]]);
    });

    it('show when they fail, and keep the calls around them apart', () => {
        const rows = consolidateSteps([
            call(1, 'Reading file: a.txt'), memory(2, false), call(3, 'Reading file: a.txt'),
        ], false);

        expect(rows.map((r) => r.tool)).toEqual(['read_file', 'remember', 'read_file']);
    });
});

describe('timeline', () => {
    const note = (id: number, text: string): AgentStep => ({
        id, type: 'commentary', label: 'Commentary', detail: text, active: false, timestamp: id,
    });
    const skill = (id: number, name: string): AgentStep =>
        call(id, `Loading skill: ${name}`, { tool: 'load_skill' });

    it('shows the agent\'s notes in full, in order, with the steps between them folded', () => {
        const steps = [
            note(1, 'Checking the forecast first.'),
            call(2, 'Reading web page: nps.gov', { tool: 'fetch_page' }),
            call(3, 'Reading file: a.txt'),
            note(4, 'Now the minutes.'),
            call(5, 'Reading file: minutes.html'),
        ];
        const { container } = render(<AgentActivity steps={steps} isActive={false} />);

        const items = [...container.querySelector('.agent-timeline')!.children].map((el) => el.textContent);
        expect(items).toEqual([
            'Checking the forecast first.',
            'Read web page, read file',
            'Now the minutes.',
            'Reading file: minutes.html',
        ]);
    });

    it('keeps the stretch that is running open, and folds it once a note follows', () => {
        const first = [note(1, 'Looking around.'), call(2, 'Reading file: a.txt'), call(3, 'Reading file: b.txt')];
        const { rerender } = render(<AgentActivity steps={first} isActive={false} running />);
        expect(screen.getByText('Reading file: a.txt')).toBeInTheDocument();

        rerender(<AgentActivity steps={[...first, note(4, 'Found it.')]} isActive={false} running />);
        expect(screen.queryByText('Reading file: a.txt')).toBeNull();
        expect(screen.getByRole('button', { name: /^Read file ×2$/ })).toHaveAttribute('aria-expanded', 'false');
    });

    it('leaves out a note the answer repeats', () => {
        const steps = [note(1, 'Dropped the scratch table.'), call(2, 'Dropping table', { tool: 'drop_table' })];
        const { container } = render(
            <AgentActivity steps={steps} isActive={false} answer={'Dropped the scratch table.\n\nAll clean.'} />,
        );

        expect(container.querySelector('.flow-note')).toBeNull();
    });

    it('names the skill the agent picked up', () => {
        render(<AgentActivity steps={[skill(1, 'meeting-notes'), call(2, 'Reading file: a.txt')]} isActive={false} />);

        expect(screen.getByRole('button', { name: /^Used meeting-notes skill, read file$/ })).toBeInTheDocument();
    });

    it('marks a skill pickup apart from ordinary calls', () => {
        const { container } = render(<AgentActivity steps={[skill(1, 'meeting-notes')]} isActive={false} />);

        expect(container.querySelector('.flow-tool')).toHaveClass('skill');
        expect(activityCss).toMatch(/\.flow-tool\.skill [^{]*\{[^}]*color: var\(--accent\)/);
    });

    it.each([
        [true, undefined, 'Thinking'],
        [false, 8_400, 'Thought for 8s'],
        [false, 400, 'Thought'],
        [false, undefined, 'Thought'],
    ])('labels reasoning (live %s, %s ms) as "%s"', (live, ms, label) => {
        expect(thoughtLabel(live, ms)).toBe(label);
    });
});

describe('timeline review fixes', () => {
    const piece = (id: number, timestamp: number, opts: Partial<AgentStep> = {}): AgentStep => ({
        id, type: 'thinking', label: 'Thinking', detail: `part ${id} `, active: false, timestamp, ...opts,
    });
    const note = (id: number, text: string): AgentStep => ({
        id, type: 'commentary', label: 'Commentary', detail: text, active: false, timestamp: id,
    });

    it('times a saved stretch of reasoning from its first piece to the next step', () => {
        const steps = [piece(1, 1_000), piece(2, 3_000), piece(3, 4_000), call(4, 'Reading file: a.txt', { timestamp: 9_000 })];

        expect(thoughtDurations(steps).get(3)).toBe(8_000);
        render(<AgentActivity steps={steps} isActive={false} />);
        fireEvent.click(screen.getByRole('button', { name: /^Read file$/ }));
        expect(screen.getByRole('button', { name: /Thought for 8s/ })).toBeInTheDocument();
    });

    it('times the reasoning before the answer from the time saved on its last piece', () => {
        const steps = [piece(1, 1_000), piece(2, 2_000, { latencyMs: 6_000 })];

        render(<AgentActivity steps={steps} isActive={false} />);
        expect(screen.getByRole('button', { name: /Thought for 6s/ })).toBeInTheDocument();
    });

    it('filters a lone call between notes like any other', () => {
        const many = [1, 2, 3, 4, 5, 6].map((i) => call(i, `Reading file: ${i}.txt`, i === 1 ? { success: false } : {}));
        render(<AgentActivity steps={[...many, note(7, 'One more.'), call(8, 'Reading file: last.txt')]} isActive={false} />);
        fireEvent.click(screen.getByRole('button', { name: /^Read file ×6$/ }));
        expect(screen.getByText('Reading file: last.txt')).toBeInTheDocument();

        fireEvent.change(screen.getByLabelText('Filter by status'), { target: { value: 'error' } });
        expect(screen.queryByText('Reading file: last.txt')).toBeNull();
        expect(screen.getByText('Reading file: 1.txt')).toBeInTheDocument();
    });

    it('shows a warning raised while the turn runs', () => {
        const warning: AgentStep = {
            id: 2, type: 'status', label: 'Detected a repeating call. Execution paused.', active: false, success: false, timestamp: 2,
        };
        render(<AgentActivity steps={[call(1, 'Reading file: a.txt'), warning]} isActive={false} running />);

        expect(screen.getByRole('status')).toHaveTextContent('Execution paused.');
    });

    it('keeps a short note the answer only mentions later on', () => {
        const { container } = render(
            <AgentActivity steps={[note(1, 'Done.'), call(2, 'Reading file: a.txt')]} isActive={false} answer="Here it is. Done." />,
        );

        expect(container.querySelector('.flow-note')).toHaveTextContent('Done.');
    });
});

it('plays entry motion only on steps arriving live, never when a folded run is opened', () => {
    const rules = [...activityCss.matchAll(/([^{}]+)\{[^}]*animation:\s*(toolSlideIn|toolStatusPop|errorSlideIn|thoughtSlideIn)\b/g)];

    expect(rules.length).toBeGreaterThan(0);
    for (const [, selector] of rules) {
        expect(selector.trim()).toMatch(/\.flow-steps\.live|\.agent-activity\.active/);
    }
});

describe('step icons', () => {
    it('show what a finished step did, not a checkmark', () => {
        const steps = [
            call(1, 'Searching web: weather', { tool: 'web_search' }),
            call(2, 'Reading web page: nps.gov', { tool: 'fetch_page' }),
        ];
        const { container } = render(<AgentActivity steps={steps} isActive={false} />);
        fireEvent.click(screen.getByRole('button', { name: /^Searched web, read web page$/ }));

        const icons = [...container.querySelectorAll('.flow-tool-left > svg')].map((svg) => svg.getAttribute('class'));
        expect(icons[0]).toContain('lucide-globe');
        expect(icons[1]).toContain('lucide-link');
        expect(container.querySelector('.flow-tool .lucide-circle-check')).toBeNull();
    });

    it('keep the warning icon for a failed step', () => {
        const { container } = render(
            <AgentActivity steps={[call(1, 'Reading web page: nps.gov', { tool: 'fetch_page', success: false })]} isActive={false} />,
        );

        expect(container.querySelector('.flow-tool-left > svg')?.getAttribute('class')).toContain('lucide-circle-alert');
    });
});
