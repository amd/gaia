// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A turn blocked on an approval must not read as a command still running (#5705). */

import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import type { AgentStep } from '../../types';

const runningShell: AgentStep = {
    id: 1, type: 'tool', label: 'run_shell_command', tool: 'run_shell_command',
    active: true, timestamp: Date.now() - 30_000,
};

const live = () => document.querySelector('.agent-summary-live')?.textContent;

describe('AgentActivity live status', () => {
    it('times a running tool', () => {
        render(<AgentActivity steps={[runningShell]} isActive />);
        expect(live()).toMatch(/^Running command · \d+s$/);
    });

    it('says it is waiting on the user, with no timer, while an approval is open', () => {
        render(<AgentActivity steps={[runningShell]} isActive awaitingUser />);
        expect(live()).toBe('Waiting for you');
    });
});
