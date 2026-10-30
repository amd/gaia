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

it('keeps folded steps out of the tab order and the accessibility tree', () => {
    render(<AgentActivity steps={steps} isActive={false} />);
    const summary = screen.getByRole('button', { name: /Ran command, wrote file/ });

    expect(summary).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: /Ran command$/ })).toBeNull();
    fireEvent.click(summary);
    expect(summary).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: /Ran command$/ })).toBeInTheDocument();
});

it('names the icon-only copy button on command output', () => {
    render(<AgentActivity steps={steps} isActive={false} />);
    fireEvent.click(screen.getByRole('button', { name: /Ran command, wrote file/ }));
    fireEvent.click(screen.getByRole('button', { name: /Ran command$/ }));

    expect(screen.getByRole('button', { name: 'Copy command output' })).toBeInTheDocument();
});
