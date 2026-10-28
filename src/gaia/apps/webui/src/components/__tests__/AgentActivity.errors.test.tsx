// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
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
