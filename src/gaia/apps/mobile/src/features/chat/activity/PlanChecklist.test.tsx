// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react-native';
import type { ActivityRow } from '@webui/utils/agentActivity.core';

import { ThemeProvider } from '@/ui';

import { PlanChecklist } from './PlanChecklist';

describe('PlanChecklist', () => {
    it('counts the done items and names each item by its status', async () => {
        const step = {
            id: 1,
            type: 'plan',
            timestamp: 0,
            label: 'Plan',
            planSteps: ['Find the file', 'Read it', 'Summarize'],
            planStatuses: ['done', 'running', 'pending'],
        } as ActivityRow;
        await render(<PlanChecklist step={step} />, { wrapper: ThemeProvider });
        expect(screen.getByText('1 of 3 done')).toBeTruthy();
        expect(screen.getByLabelText('Done: Find the file')).toBeTruthy();
        expect(screen.getByLabelText('Running: Read it')).toBeTruthy();
        expect(screen.getByLabelText('Not started: Summarize')).toBeTruthy();
    });
});
