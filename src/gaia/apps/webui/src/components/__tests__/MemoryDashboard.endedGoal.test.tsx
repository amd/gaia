// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** An ended goal with no steps doesn't claim the agent will break it down when it starts. */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryDashboard } from '../MemoryDashboard';
import * as memoryApi from '../../services/memoryApi';

vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

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

async function openGoals(status: 'queued' | 'failed') {
    mockedApi.listGoals.mockResolvedValue({
        goals: [{
            id: 'g1', title: 'Plan a vegetarian week of dinners', description: 'Five easy dinners.', status,
            source: 'user', mode_required: 'manual', approved_for_auto: true, priority: 'medium',
            progress_notes: status === 'failed' ? 'The background run failed: it needs your approval.' : '',
            created_at: '', updated_at: '', tasks: [],
        }],
        total: 1,
    } as never);
    render(<MemoryDashboard />);
    await screen.findByRole('heading', { name: 'What GAIA knows about you' });
    fireEvent.click(screen.getByRole('tab', { name: /Goals/ }));
    fireEvent.click((await screen.findByText('Plan a vegetarian week of dinners')).closest('[role="button"]') as HTMLElement);
}

describe('Goal with no steps', () => {
    it('says the agent will break it down while the goal is waiting to start', async () => {
        await openGoals('queued');
        expect(screen.getByText(/the agent will break this down when it starts/)).toBeInTheDocument();
    });

    it('does not promise that once the goal has failed', async () => {
        await openGoals('failed');
        expect(screen.getByText(/it needs your approval/)).toBeInTheDocument();
        expect(screen.queryByText(/will break this down when it starts/)).not.toBeInTheDocument();
    });
});
