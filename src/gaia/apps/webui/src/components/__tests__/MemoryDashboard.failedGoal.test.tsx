// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A failed goal says so in words and can be put back in the queue. */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryDashboard } from '../MemoryDashboard';
import * as memoryApi from '../../services/memoryApi';

vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

const goal = (status: string) => ({
    id: 'g1', title: 'Plan a trip to Lisbon', description: '', status,
    source: 'user', mode_required: 'manual', approved_for_auto: true, priority: 'medium',
    progress_notes: '', created_at: '', updated_at: '', tasks: [],
});

beforeEach(() => {
    vi.resetAllMocks();
    mockedApi.getMemoryStats.mockResolvedValue({
        knowledge: { total: 0, total_retrievals: 0, by_category: {}, by_context: {}, sensitive_count: 0, entity_count: 0, avg_confidence: 0, oldest: null, newest: null },
        conversations: { total_turns: 0, total_sessions: 0, first_session: null, last_session: null },
        tools: { total_calls: 0, unique_tools: 0, overall_success_rate: 0, total_errors: 0 },
        temporal: { upcoming_count: 0, overdue_count: 0 },
        db_size_bytes: 0,
    });
    mockedApi.getMemoryActivity.mockResolvedValue([]);
    mockedApi.getKnowledge.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 200 });
    mockedApi.getToolSummary.mockResolvedValue([]);
    mockedApi.getMemoryConversations.mockResolvedValue([]);
    mockedApi.getUpcomingItems.mockResolvedValue([]);
    mockedApi.getEmbeddingCoverage.mockResolvedValue({ total_items: 0, with_embedding: 0, without_embedding: 0, coverage_pct: 0 });
    mockedApi.getEntities.mockResolvedValue([]);
    mockedApi.listGoals.mockResolvedValue({ goals: [], total: 0 });
    mockedApi.getGoalStats.mockResolvedValue({ goals: {}, tasks: {} });
    mockedApi.getMemorySettings.mockResolvedValue({ memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false });
});

async function openGoals(status: string) {
    mockedApi.listGoals.mockResolvedValue({ goals: [goal(status)], total: 1 } as never);
    render(<MemoryDashboard />);
    await screen.findByRole('heading', { name: 'What GAIA knows about you' });
    fireEvent.click(screen.getByRole('tab', { name: /Goals/ }));
    return (await screen.findByText('Plan a trip to Lisbon')).closest('.mem-goal-card') as HTMLElement;
}

describe('Failed goal row', () => {
    it('says failed and offers Try again', async () => {
        const card = await openGoals('failed');
        expect(within(card).getByText('failed')).toBeInTheDocument();
        expect(within(card).getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    });

    it('queues the goal again and reloads the list when Try again is pressed', async () => {
        mockedApi.retryGoal.mockResolvedValue(goal('queued') as never);
        const card = await openGoals('failed');
        mockedApi.listGoals.mockResolvedValue({ goals: [goal('queued')], total: 1 } as never);
        fireEvent.click(within(card).getByRole('button', { name: 'Try again' }));
        await waitFor(() => expect(mockedApi.retryGoal).toHaveBeenCalledWith('g1'));
        await waitFor(() => {
            const row = screen.getByText('Plan a trip to Lisbon').closest('.mem-goal-card') as HTMLElement;
            expect(within(row).queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
            expect(within(row).getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
        });
    });

    it('offers no Try again on a goal that has not failed', async () => {
        const card = await openGoals('queued');
        expect(within(card).queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
    });
});
