// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Goals and Profile setup say what they are, what they read and where they run, before a click. */

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

async function openTab(name: RegExp) {
    render(<MemoryDashboard />);
    await screen.findByRole('heading', { name: 'What GAIA knows about you' });
    fireEvent.click(screen.getByRole('tab', { name }));
}

describe('Goals', () => {
    it('says what a goal is, where it runs, and how it differs from a scheduled task', async () => {
        await openTab(/Goals/);

        const intro = await screen.findByText(/A goal is a bigger job/);
        expect(intro).toHaveTextContent('Background goals');
        expect(intro).toHaveTextContent('Scheduled tasks');
    });

    it('no longer points at an "autonomous mode" the app does not have', async () => {
        await openTab(/Goals/);

        expect(await screen.findByText(/No goals yet/)).not.toHaveTextContent(/autonomous mode/);
    });
});

describe('Profile setup', () => {
    it('lists what each scan reads, says it stays on this PC and that you review before saving', async () => {
        await openTab(/Profile setup/);

        expect(await screen.findByText(/run on this PC and send nothing anywhere/)).toHaveTextContent(/before anything is saved/);
        expect(screen.getByText(/Reads installed apps.*browser bookmarks and the last 30 days of history/)).toBeInTheDocument();
        expect(screen.getByText(/asks a model on this PC/)).toBeInTheDocument();
        expect(screen.getByText(/never a cloud one/)).toBeInTheDocument();
        expect(screen.getByLabelText(/last 30 days of browser history/)).not.toBeChecked();
        expect(screen.queryByText(/Lemonade Server/)).toBeNull();
        expect(screen.queryByRole('button', { name: 'Run Inference' })).toBeNull();
    });
});
