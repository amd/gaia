// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AgentActivity, toolActiveLabel } from '../AgentActivity';
import type { AgentStep } from '../../types';

vi.mock('../../services/api');

const LOG = Array.from({ length: 8 }, (_, i) => `Commit: abc${i}\nMessage: change ${i}`).join('\n');

function gitLog(overrides: Partial<AgentStep> = {}): AgentStep {
    return {
        id: 1, type: 'tool', label: 'Using mcp_git_git_log', tool: 'mcp_git_git_log',
        narration: 'Running mcp git git log', mcpServer: 'mcp-git', mcpServerLabel: 'Git', mcpTool: 'git_log',
        active: false, success: true, result: 'Commit: abc0 (+15 more lines)',
        mcpOutput: { text: LOG, truncated: false }, timestamp: 1,
        ...overrides,
    };
}

it('names the connector and tool on an MCP step, not "MCP tool"', () => {
    render(<AgentActivity steps={[gitLog()]} isActive={false} variant="inline" />);

    expect(screen.getByText('Used Git: git_log')).toBeInTheDocument();
    expect(screen.queryByText('MCP tool')).toBeNull();
    expect(screen.queryByText('Running mcp git git log')).toBeNull();
});

it('names the connector while the MCP call runs', () => {
    expect(toolActiveLabel('mcp_git_git_log', { mcpServerLabel: 'Git', mcpTool: 'git_log' }))
        .toBe('Using Git: git_log');
});

it('shows the first lines of an MCP tool\'s output and expands to all of it', () => {
    render(<AgentActivity steps={[gitLog()]} isActive={false} variant="inline" />);
    fireEvent.click(screen.getByRole('button', { name: /Used Git: git_log/ }));

    const output = screen.getByTestId('mcp-output');
    expect(output.textContent).toContain('Commit: abc0');
    expect(output.textContent).not.toContain('Commit: abc7');
    expect(screen.queryByText(/returned data/)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Show all 16 lines' }));

    expect(screen.getByTestId('mcp-output').textContent).toContain('Message: change 7');
});

it('keeps the old label for a saved MCP step that has no connector name', () => {
    const legacy = gitLog({ mcpServerLabel: undefined, mcpTool: undefined, narration: undefined, mcpOutput: undefined });
    render(<AgentActivity steps={[legacy]} isActive={false} variant="inline" />);

    expect(screen.getByText('MCP tool')).toBeInTheDocument();
});
