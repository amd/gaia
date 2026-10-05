// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import type { AgentStep } from '../../types';

vi.mock('../../services/api');

const steps: AgentStep[] = [
    {
        id: 1, type: 'tool', label: 'Using tool', tool: 'run_shell_command', active: false, success: true, timestamp: 1,
        commandOutput: { command: 'dir', stdout: 'a.txt', stderr: '', returnCode: 0 },
    },
    { id: 2, type: 'tool', label: 'Using tool', tool: 'write_file', active: false, success: true, timestamp: 2 },
];

it('keeps collapsed activity out of the tab order and the accessibility tree', () => {
    const { container } = render(<AgentActivity steps={steps} isActive={false} variant="summary" />);
    const wrap = container.querySelector('.agent-flow-wrap')!;

    expect(wrap.hasAttribute('inert')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Expand agent activity' }));
    expect(wrap.hasAttribute('inert')).toBe(false);
});

it('names the icon-only copy button on command output', () => {
    render(<AgentActivity steps={steps} isActive={false} variant="summary" />);
    fireEvent.click(screen.getByRole('button', { name: 'Expand agent activity' }));

    expect(screen.getByRole('button', { name: 'Copy command output' })).toBeInTheDocument();
});
