// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import type { AgentStep } from '../../types';

vi.mock('../../services/api');

const NOTICE = 'Moved 3 older tool results out of the prompt (~12.4K tokens); still readable';

it('keeps a context reduction notice when status updates follow it', () => {
    const steps: AgentStep[] = [
        { id: 1, type: 'status', label: 'Reading the files', active: false, timestamp: 1 },
        { id: 2, type: 'status', label: NOTICE, active: false, notice: true, timestamp: 2 },
        { id: 3, type: 'status', label: 'Working out how to answer', active: true, timestamp: 3 },
    ];
    render(<AgentActivity steps={steps} isActive variant="summary" />);

    const notice = screen.getByText(NOTICE);
    expect(notice.closest('.flow-notice')).not.toBeNull();
    expect(screen.getByText('Working out how to answer')).toBeInTheDocument();
});
