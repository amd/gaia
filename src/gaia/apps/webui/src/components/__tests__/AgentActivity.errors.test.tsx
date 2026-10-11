// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import type { AgentStep } from '../../types';

vi.mock('../../services/api');

const failure = "the daemon refused to ensure the 'gaia' sidecar";

it('shows a failed turn\'s error without a click', () => {
    const steps: AgentStep[] = [
        { id: 1, type: 'error', label: 'Error', detail: failure, active: false, success: false, timestamp: 1 },
    ];
    const { container } = render(<AgentActivity steps={steps} isActive={false} variant="summary" />);

    expect(container.querySelector('.agent-flow-wrap')!.hasAttribute('inert')).toBe(false);
    expect(screen.getByRole('button', { name: 'Collapse agent activity' })).toBeInTheDocument();
    expect(screen.getByText(failure)).toBeVisible();
});

it('keeps a turn whose only failure is a tool result collapsed', () => {
    const steps: AgentStep[] = [
        { id: 1, type: 'tool', label: 'Using tool', tool: 'read_file', active: false, success: false, timestamp: 1 },
    ];
    const { container } = render(<AgentActivity steps={steps} isActive={false} variant="summary" />);

    expect(container.querySelector('.agent-flow-wrap')!.hasAttribute('inert')).toBe(true);
    expect(screen.getByRole('button', { name: 'Expand agent activity' })).toBeInTheDocument();
});
