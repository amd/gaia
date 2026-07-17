// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Contract tests for the `needs_confirmation` AgentStep type (issue #2109,
 * increment 4): a sidecar agent action awaiting confirmation.
 *
 *   - A step with `type: 'needs_confirmation'` auto-expands the panel
 *     (mirroring the existing `hasPolicyAlerts` auto-expand), without any
 *     click, via `hasNeedsConfirmation` OR'd into the same
 *     `useEffect`/`expanded` state.
 *   - It renders inline via a `FlowNeedsConfirmation` block reusing
 *     `FlowPolicyAlert`'s CSS classes: a "Confirmation needed" header, an
 *     "Action" line showing `step.action`, and a body/reason line showing
 *     `step.summary` (falling back to `step.detail`).
 *   - Ordinary step types (no `policy_alert` / `needs_confirmation`) stay
 *     collapsed by default — guards that auto-expand doesn't fire
 *     unconditionally.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AgentActivity } from '../AgentActivity';
import type { AgentStep } from '../../types';

const needsConfirmationStep: AgentStep = {
    id: 1,
    type: 'needs_confirmation',
    label: 'Needs confirmation',
    action: 'Archive 12 messages',
    summary: 'Archive 12 low-priority messages older than 30 days.',
    detail: 'fallback detail text (must not be used — summary is set)',
    timestamp: 1234567890,
};

const ordinaryToolStep: AgentStep = {
    id: 1,
    type: 'tool',
    label: 'Used tool',
    tool: 'search_file',
    timestamp: 1234567890,
};

describe('AgentActivity needs_confirmation auto-expand (#2109)', () => {
    it('auto-expands the panel when a step has type needs_confirmation, without any click', () => {
        const { container } = render(<AgentActivity steps={[needsConfirmationStep]} isActive={false} />);

        const toggle = container.querySelector('.agent-summary-bar');
        const flowWrap = container.querySelector('.agent-flow-wrap');

        expect(toggle).not.toBeNull();
        expect(toggle).toHaveAttribute('aria-expanded', 'true');
        expect(flowWrap?.classList.contains('flow-expanded')).toBe(true);
    });

    it('shows the action and summary text inline for a needs_confirmation step', () => {
        render(<AgentActivity steps={[needsConfirmationStep]} isActive={false} />);

        expect(screen.getByText('Confirmation needed')).toBeInTheDocument();
        expect(screen.getByText('Action')).toBeInTheDocument();
        expect(screen.getByText('Archive 12 messages')).toBeInTheDocument();
        expect(
            screen.getByText('Archive 12 low-priority messages older than 30 days.'),
        ).toBeInTheDocument();
    });

    it('stays collapsed by default for ordinary step types (no policy_alert / needs_confirmation)', () => {
        const { container } = render(<AgentActivity steps={[ordinaryToolStep]} isActive={false} />);

        const toggle = container.querySelector('.agent-summary-bar');
        expect(toggle).not.toBeNull();
        expect(toggle).toHaveAttribute('aria-expanded', 'false');
    });
});
