// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A stopped answer reloaded from the server still says it was stopped (#6537). */

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { fromApiMessages } from '../chatStream';
import { MessageBubble } from '../../components/MessageBubble';

const row = (extra: Record<string, unknown> = {}) => ({
    id: 7, session_id: 's', role: 'assistant', content: 'Fold it twice, then sand it by hand. G',
    created_at: '2026-11-03T00:00:00Z', rag_sources: null, ...extra,
});

describe('fromApiMessages', () => {
    it('marks a reply the server saved as stopped', () => {
        expect(fromApiMessages([row({ stopped: true })])[0].stopState).toBe('stopped');
    });

    it('leaves a finished reply unmarked', () => {
        expect(fromApiMessages([row({ stopped: false })])[0].stopState).toBeUndefined();
        expect(fromApiMessages([row()])[0].stopState).toBeUndefined();
    });

    it('shows the Stopped chip and footer after a reload, not "Answered in"', () => {
        const [message] = fromApiMessages([row({ stopped: true })]);
        render(<MessageBubble message={message} latencyMs={8_000} />);
        expect(screen.getByRole('note')).toHaveTextContent('Stopped');
        expect(screen.getByRole('button', { name: 'Stopped after 8 s' })).toBeInTheDocument();
        expect(document.body).not.toHaveTextContent('Answered in');
    });

    it("reads a saved command's whole text, output and exit code back under the view's names", () => {
        const command = `cd /work && ${'python3 -m pytest tests/test_x.py -x && '.repeat(8)}echo done`;
        const [message] = fromApiMessages([row({ agent_steps: [{
            id: 1, type: 'tool', label: 'Using run_shell_command', tool: 'run_shell_command',
            detail: `command: ${command.slice(0, 150)}...`, result: 'line 0', success: true,
            commandOutput: {
                command, stdout: 'line 0\nline 1\n', stderr: '', return_code: 0, cwd: '/work',
                duration_seconds: 1.5, truncated: false,
            },
        }] })]);
        expect(message.agentSteps?.[0].commandOutput).toEqual({
            command, stdout: 'line 0\nline 1\n', stderr: '', returnCode: 0, cwd: '/work',
            durationSeconds: 1.5, truncated: false, expectedExit: undefined,
        });
    });

    it('leaves a command step that is already under those names alone', () => {
        const commandOutput = { command: 'ls', stdout: 'a\n', stderr: '', returnCode: 2 };
        const [message] = fromApiMessages([row({ agentSteps: [{ id: 1, type: 'tool', label: 'x', commandOutput }] })]);
        expect(message.agentSteps?.[0].commandOutput).toEqual(commandOutput);
    });
});
