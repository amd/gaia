// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The terminal copy button says "Copied" only when the clipboard took the text.

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import { log } from '../../utils/logger';
import type { AgentStep } from '../../types';

const steps: AgentStep[] = [
    {
        id: 1, type: 'tool', label: 'Using tool', tool: 'run_shell_command', active: false, success: true, timestamp: 1,
        commandOutput: { command: 'dir', stdout: 'a.txt', stderr: '', returnCode: 0 },
    },
    { id: 2, type: 'tool', label: 'Using tool', tool: 'write_file', active: false, success: true, timestamp: 2 },
];

function openCopyButton() {
    render(<AgentActivity steps={steps} isActive={false} />);
    fireEvent.click(screen.getByRole('button', { name: /Ran command, wrote file/ }));
    fireEvent.click(screen.getByRole('button', { name: /Ran command$/ }));
    return screen.getByRole('button', { name: 'Copy command output' });
}

afterEach(() => {
    vi.restoreAllMocks();
});

describe('command output copy', () => {
    it('shows Copied after the clipboard accepts the text', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.assign(navigator, { clipboard: { writeText } });

        fireEvent.click(openCopyButton());

        expect(await screen.findByRole('button', { name: 'Copied to clipboard' })).toBeInTheDocument();
        expect(writeText).toHaveBeenCalledWith('a.txt');
        expect(screen.queryByRole('alert')).toBeNull();
    });

    it('logs and shows a failure instead of Copied when the clipboard refuses', async () => {
        const warn = vi.spyOn(log.ui, 'warn').mockImplementation(() => {});
        const writeText = vi.fn().mockRejectedValue(new Error('denied'));
        Object.assign(navigator, { clipboard: { writeText } });

        fireEvent.click(openCopyButton());

        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't copy the output");
        expect(screen.queryByRole('button', { name: 'Copied to clipboard' })).toBeNull();
        expect(warn).toHaveBeenCalledWith(expect.stringContaining('Could not copy'), expect.any(Error));
    });
});
