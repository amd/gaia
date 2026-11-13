// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import type { AgentStep } from '../../types';

vi.mock('../../services/api');

const failure = "GAIA couldn't start its agent. Send your message again.";

it('shows a failed turn\'s error without a click', () => {
    const steps: AgentStep[] = [
        { id: 1, type: 'error', label: 'Error', detail: failure, active: false, success: false, timestamp: 1 },
    ];
    render(<AgentActivity steps={[...steps, { ...steps[0], id: 2, type: 'thinking', detail: 'Retrying.' }]} isActive={false} />);

    expect(screen.getByRole('button', { name: /Hit an error/ })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText(failure)).toBeVisible();
});

it('keeps steps whose only failure is a tool result folded', () => {
    const steps: AgentStep[] = [
        { id: 1, type: 'tool', label: 'Using tool', tool: 'read_file', active: false, success: false, timestamp: 1 },
        { id: 2, type: 'tool', label: 'Using tool', tool: 'read_file', detail: 'b', active: false, success: true, timestamp: 2 },
    ];
    render(<AgentActivity steps={steps} isActive={false} />);

    expect(screen.getByRole('button', { name: /Read file ×2/ })).toHaveAttribute('aria-expanded', 'false');
});

it('says a folded run with a policy block was blocked by policy', () => {
    const steps: AgentStep[] = [
        { id: 1, type: 'policy_alert', label: 'Policy blocked run_shell_command', tool: 'run_shell_command', decision: 'BLOCK', reason: 'Not allowed here.', ruleIds: [], active: false, success: false, timestamp: 1 },
        { id: 2, type: 'thinking', label: 'Thinking', detail: 'Try another way.', active: false, timestamp: 2 },
    ];
    render(<AgentActivity steps={steps} isActive={false} />);

    expect(screen.getByRole('button', { name: /^Blocked by policy$/ })).toHaveAttribute('aria-expanded', 'true');
});

it('does not head a run whose only memory call failed "Saved to memory"', () => {
    const steps: AgentStep[] = [
        { id: 1, type: 'thinking', label: 'Thinking', detail: 'Store it as sensitive.', active: false, timestamp: 1 },
        { id: 2, type: 'tool', label: 'Using remember', tool: 'remember', active: false, success: false, result: 'Invalid category', timestamp: 2 },
        { id: 3, type: 'thinking', label: 'Thinking', detail: 'Use another category.', active: false, timestamp: 3 },
    ];
    render(<AgentActivity steps={steps} isActive={false} />);

    expect(screen.getByRole('button', { name: /Saving to memory failed/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Saved to memory/ })).toBeNull();
});

it('says a blocked page fetch in plain words and keeps the site name on the row', () => {
    const steps: AgentStep[] = [
        {
            id: 1, type: 'tool', label: 'Using fetch_page', tool: 'fetch_page', active: false, success: false, timestamp: 1,
            narration: 'Fetching page: accuweather.com/…/paris/623',
            result: 'Error fetching page: 403 Client Error: Forbidden for url: https://www.accuweather.com/en/fr/paris/623',
        },
    ];
    const { container } = render(<AgentActivity steps={steps} isActive={false} />);
    fireEvent.click(screen.getAllByRole('button')[0]);

    expect(screen.getByText('Site blocked it (403)')).toBeVisible();
    expect(screen.getByText('Fetching page: accuweather.com/…/paris/623')).toBeVisible();
    expect(container.querySelector('.flow-tool-failure')).toHaveTextContent('Site blocked it (403)');
});
