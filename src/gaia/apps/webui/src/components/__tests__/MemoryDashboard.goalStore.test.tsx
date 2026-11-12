// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A goal list GAIA can't read says so on the form and the page, and offers the fix it has (#7739). */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryDashboard } from '../MemoryDashboard';
import * as memoryApi from '../../services/memoryApi';
import { useGoalHealthStore } from '../../stores/goalHealthStore';

vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);
const DAMAGED = "GAIA's goal list is damaged (file is not a database), so goals can't be shown or saved. Start a new goal list on this page; the damaged file is kept.";

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
    mockedApi.getGoalStoreHealth.mockResolvedValue({ error: null, damaged: false });
    useGoalHealthStore.setState({ error: null, damaged: false });
});

async function openGoals() {
    render(<MemoryDashboard />);
    await screen.findByRole('heading', { name: 'What GAIA knows about you' });
    fireEvent.click(screen.getByRole('tab', { name: /Goals/ }));
}

describe('Goals with a goal list GAIA cannot read', () => {
    it('says on the form why Create did not save, and keeps what was typed', async () => {
        mockedApi.createGoal.mockRejectedValue(new Error(DAMAGED));
        await openGoals();
        fireEvent.click(await screen.findByRole('button', { name: /New Goal/ }));
        fireEvent.change(screen.getByPlaceholderText(/title/i), { target: { value: 'Tidy my notes' } });
        fireEvent.click(screen.getByRole('button', { name: 'Create' }));

        const form = document.querySelector('.mem-goals-add-form') as HTMLElement;
        const alert = await within(form).findByRole('alert');
        expect(alert).toHaveTextContent("Couldn't create the goal");
        expect(alert).toHaveTextContent('file is not a database');
        expect(screen.getByDisplayValue('Tidy my notes')).toBeInTheDocument();
    });

    it('says the goals could not be loaded instead of "No goals yet"', async () => {
        mockedApi.listGoals.mockRejectedValue(new Error(DAMAGED));
        await openGoals();

        expect(await screen.findByText(/Couldn't load your goals/)).toHaveTextContent('file is not a database');
        expect(screen.queryByText(/No goals yet/)).toBeNull();
    });

    it('offers a new goal list for a damaged file, not a restart', async () => {
        mockedApi.getGoalStoreHealth.mockResolvedValue({ error: DAMAGED, damaged: true });
        useGoalHealthStore.setState({ error: DAMAGED, damaged: true });
        mockedApi.resetGoalStore.mockResolvedValue({ kept: 'goals.db.damaged-20261112-150000' });
        await openGoals();

        const banner = await screen.findByText(/goal list is damaged/);
        expect(banner.closest('[role="alert"]')).not.toHaveTextContent(/restart/i);
        mockedApi.getGoalStoreHealth.mockResolvedValue({ error: null, damaged: false });
        fireEvent.click(screen.getByRole('button', { name: 'Start a new goal list' }));

        await waitFor(() => expect(mockedApi.resetGoalStore).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(screen.queryByText(/goal list is damaged/)).toBeNull());
        expect(await screen.findByText(/goals\.db\.damaged-20261112-150000/)).toBeInTheDocument();
    });
});
