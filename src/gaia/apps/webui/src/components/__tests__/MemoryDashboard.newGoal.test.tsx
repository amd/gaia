// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The New Goal form's Create button is off until the goal has a title. */

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

async function openForm() {
    render(<MemoryDashboard />);
    await screen.findByRole('heading', { name: 'What GAIA knows about you' });
    fireEvent.click(screen.getByRole('tab', { name: /Goals/ }));
    fireEvent.click(await screen.findByRole('button', { name: /New Goal/ }));
}

describe('New Goal form', () => {
    it('keeps Create off while the title is empty or blank', async () => {
        await openForm();
        const create = screen.getByRole('button', { name: 'Create' });
        expect(create).toBeDisabled();
        fireEvent.change(screen.getByPlaceholderText('Goal title'), { target: { value: '   ' } });
        expect(create).toBeDisabled();
    });

    it('turns Create on once there is a title', async () => {
        await openForm();
        fireEvent.change(screen.getByPlaceholderText('Goal title'), { target: { value: 'Learn guitar' } });
        expect(screen.getByRole('button', { name: 'Create' })).toBeEnabled();
    });
});
