// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryDashboard } from '../MemoryDashboard';
import * as memoryApi from '../../services/memoryApi';

vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

function stats(tools: { total_calls: number; unique_tools: number; overall_success_rate: number; total_errors: number }) {
    return {
        knowledge: {
            total: 0, total_retrievals: 0, by_category: {}, by_context: {}, sensitive_count: 0,
            entity_count: 0, avg_confidence: 0, oldest: null, newest: null,
        },
        conversations: { total_turns: 0, total_sessions: 0, first_session: null, last_session: null },
        tools,
        temporal: { upcoming_count: 0, overdue_count: 0 },
        db_size_bytes: 0,
    };
}

beforeEach(() => {
    vi.resetAllMocks();
    mockedApi.getMemoryActivity.mockResolvedValue([]);
    mockedApi.getKnowledge.mockResolvedValue({ items: [], total: 0 });
    mockedApi.getToolSummary.mockResolvedValue([]);
    mockedApi.getMemoryConversations.mockResolvedValue([]);
    mockedApi.getUpcomingItems.mockResolvedValue([]);
    mockedApi.getEmbeddingCoverage.mockResolvedValue({
        total_items: 0, with_embedding: 0, without_embedding: 0, coverage_pct: 0,
    });
    mockedApi.getEntities.mockResolvedValue([]);
    mockedApi.listGoals.mockResolvedValue({ goals: [], total: 0 });
    mockedApi.getGoalStats.mockResolvedValue({ goals: {}, tasks: {} });
    mockedApi.getMemorySettings.mockResolvedValue({
        memory_enabled: true, mcp_memory_enabled: false, system_discovery_consent: false,
    });
});

async function successCard() {
    render(<MemoryDashboard />);
    const label = await screen.findByText('Success Rate');
    return label.closest('.mem-stat-card') as HTMLElement;
}

it('shows no rate, not 0%, before any tool has run', async () => {
    mockedApi.getMemoryStats.mockResolvedValue(stats({ total_calls: 0, unique_tools: 0, overall_success_rate: 0, total_errors: 0 }) as never);
    const card = await successCard();

    expect(card.querySelector('.mem-stat-value')?.textContent).toBe('—');
    expect(card).toHaveTextContent('No tool calls yet');
});

it('shows the rate and errors once tools have run', async () => {
    mockedApi.getMemoryStats.mockResolvedValue(stats({ total_calls: 4, unique_tools: 2, overall_success_rate: 0.75, total_errors: 1 }) as never);
    const card = await successCard();

    expect(await screen.findByText('75%')).toBeInTheDocument();
    expect(card).toHaveTextContent('1 errors');
});
