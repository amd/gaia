// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import type { AgentStep } from '../../types';

vi.mock('../../services/api');

function step(tool: string, active = false, success = true): AgentStep {
    return { id: 1, type: 'tool', label: `Using ${tool}`, tool, active, success, timestamp: 1 };
}

it.each([
    ['search_web', 'Searched web'],
    ['request_user_input', 'Asked you'],
    ['recall', 'Recalled memory'],
    ['remember', 'Saved to memory'],
    ['find_files', 'Found files'],
    ['search_past_conversations', 'Searched past chats'],
])('names the GAIA agent\'s %s step instead of "Used tool"', (tool, label) => {
    render(<AgentActivity steps={[step(tool)]} isActive={false} variant="summary" />);

    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText('Used tool')).toBeNull();
});

it.each([
    ['write_file', 'Writing file failed', 'Wrote file'],
    ['read_file', 'Reading file failed', 'Read file'],
    ['some_unknown_tool', 'Using tool failed', 'Used tool'],
])('does not give a failed or denied %s step the done label', (tool, label, doneLabel) => {
    render(<AgentActivity steps={[step(tool, false, false)]} isActive={false} variant="inline" />);

    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText(doneLabel)).toBeNull();
});

it('names a running GAIA agent step in the present tense', () => {
    render(<AgentActivity steps={[step('request_user_input', true)]} isActive={true} variant="inline" />);

    expect(screen.getByText('Asking you')).toBeInTheDocument();
});
